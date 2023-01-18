import base64
import hashlib
import os
import pathlib
import re
import sys
import sysconfig
import zipfile
from configparser import ConfigParser
from email.message import Message
from typing import TYPE_CHECKING, Callable, List, Optional, Sequence, Tuple, Union, NamedTuple

import packaging.requirements
import packaging.tags
import packaging.utils
import packaging.version
import pytoml
from SCons.Environment import Environment
from SCons.Errors import UserError

from enscons import pytar

if TYPE_CHECKING:
    from SCons.Node.FS import Dir, Entry, File
    from SCons.Node import Node


def urlsafe_b64encode(data):
    """urlsafe_b64encode without padding"""
    return base64.urlsafe_b64encode(data).rstrip(b"=")


DIST_NAME_RE = re.compile(
    "^([A-Z0-9]|[A-Z0-9][A-Z0-9._-]*[A-Z0-9])$", flags=re.IGNORECASE
)
EXTRA_RE = re.compile("^([a-z0-9]|[a-z0-9]([a-z0-9-](?!--))*[a-z0-9])$")
EPOINT_GROUP_RE = re.compile(r"^\w+(\.\w+)*$")
EPOINT_NAME_RE = re.compile(r"[\w.-]+")


def get_rel_path(env: Environment, src: Union[str, "Entry"]) -> str:
    """Returns the relative path to the given source file, relative to either
    the source root or the build root

    If the given source is underneath env["WHEEL_BUILD_DIR"], then the returned path is
    relative to the build subdirectory. Otherwise, it is relative to the source root.

    >>> get_rel_path(env, "foo/bar/baz.py")
        "foo/bar/baz.py"
    >>> get_rel_path(env, "build/bdir/foo/bar/baz.py")
        "foo/bar/baz.py"

    This is useful for discovering the original path to a file which may or may not be
    currently under a build directory. It is primarily used for computing the path
    for files about to be copied into the wheel. Its secondary use is to compute
    a new build directory for a given file.

    Another common use is to compute the path for files to copy from a build directory
    back into the source tree (e.g. for inplace installs of shared objects)
    """
    build_dir = env["WHEEL_BUILD_DIR"]
    src = env.Entry(src)
    path_components = src.get_path_elements()
    try:
        index = path_components.index(build_dir)
    except ValueError:
        # Is relative to the root dir
        return str(src)
    else:
        # Is relative to whichever build subdirectory it's in
        return path_components[index + 1].rel_path(src)


def get_build_path(
    env: Environment, src: Union[str, "Entry"], build_dir: Union[str, "Dir"]
) -> "File":
    """Returns the path to use for files generated from some "src" file.

    Paths returned are under a build directory named by the build_dir parameter.
    If a directory is given for the build_dir, it must be a direct subdirectory within
    env["WHEEL_BUILD_DIR"]

    This method should be used by any builders which need to output intermediate files
    from sources elsewhere (sorcues either in the source root directory or under one
    of the build directories).

    It correctly calculates the relative path so that paths relative to the source root
    are preserved.

    e.g.

    >>> get_build_path(env, "src/foo/bar.pyx", "cython")
        build/cython/src/foo/bar.c
    >>> get_build_path(env, "build/cython/src/foo/bar.c", "lib.linux-x86_64")
        build/lib.linux-x86_64/src/foo/bar.py
    """
    rel_path = get_rel_path(env, src)
    if isinstance(build_dir, str):
        build_dir = env["WHEEL_BUILD_DIR"].Dir(build_dir)
    elif build_dir.get_path_elements()[-2] != env["WHEEL_BUILD_DIR"]:
        raise ValueError(
            f"Build directory {build_dir.get_abspath()} is not a direct subdirectory of "
            f"{env['WHEEL_BUILD_DIR'].get_abspath()}"
        )

    full_path = build_dir.File(rel_path)
    return full_path


class PyProject(NamedTuple):
    # Validate project name
    name: str
    # Validated version
    version: str
    # The distribution name component to be used in filenames
    dist_filename: str

    # Full deserialized project table, with name and version normalized
    project_metadata: dict
    # Tool table
    tool_metadata: dict

    # The filename that it was parsed from
    file: str

def parse_pyproject_toml(file: str):
    toml = pytoml.load(open(file))
    project_metadata = toml["project"]
    try:
        tool_metadata = toml["tool"]
    except KeyError:
        tool_metadata = {}

    # Validate and normalize the name
    name = project_metadata["name"]
    if not DIST_NAME_RE.match(name):
        raise UserError(
            "Distribution name must consist of only ASCII letters, numbers, period, "
            "underscore, and hyphen. It must start and end with a letter or number. "
            f"Was {name!r}"
        )
    project_metadata["name"] = name

    # The distribution name component to be used in filenames
    dist_filename = packaging.utils.canonicalize_name(
        name
    ).replace("-", "_")

    # Check if the version is valid and normalize it
    version = str(packaging.version.parse(project_metadata["version"]))
    project_metadata["version"] = version

    return PyProject(
        name=name,
        version=version,
        dist_filename=dist_filename,
        project_metadata=project_metadata,
        tool_metadata=tool_metadata,
        file=file,
    )

def build_core_metadata(pyproject: PyProject) -> Tuple[str, List["File"]]:
    """Builds the core metadata from the parsed pyproject.toml data

    """
    # Reference: https://packaging.python.org/en/latest/specifications/core-metadata/
    sources: List[str] = [pyproject.file]
    msg = Message()
    metadata = pyproject.project_metadata

    # Required metadata
    msg["Metadata-Version"] = "2.3"
    msg["Name"] = pyproject.name
    msg["Version"] = pyproject.version

    # Optional metadata
    if "description" in metadata:
        msg["Summary"] = metadata["description"]
    if "requires-python" in metadata:
        msg["Requires-Python"] = metadata["requires-python"]

    # Readme field. May be a string referencing a file, or a table specifying a content
    # type and either a file or text.
    if "readme" in metadata:
        readme = metadata["readme"]
        if isinstance(readme, str):
            filename = readme
            contenttype = None
            content = open(filename, "r", encoding="utf-8").read()
        else:
            assert isinstance(readme, dict)
            if "file" and "text" in readme:
                raise UserError(
                    f'"file" and "text" keys are mutually exclusive in {pyproject.file} project.readme table'
                )
            if "file" in readme:
                filename = readme["file"]
                contenttype = readme.get("content-type")
                encoding = readme.get("encoding", "utf-8")
                content = open(filename, "r", encoding=encoding).read()
            else:
                filename = None
                try:
                    contenttype = readme["content-type"]
                except KeyError as e:
                    raise UserError(
                        f"Missing content-type key in {pyproject.file} project.readme table"
                    ) from e
                content = readme["text"]
        if contenttype is None:
            assert filename
            ext = os.path.splitext(filename)[1].lower()
            try:
                contenttype = {
                    ".md": "text/markdown",
                    ".rst": "text/x-rst",
                    ".txt": "text/plain",
                }[ext]
            except KeyError as e:
                raise UserError(
                    f"Unknown readme file type {filename}. "
                    f'Specify an explicit "content-type" key in the {pyproject.file} '
                    f"project.readme table"
                )
        if filename:
            sources.append(filename)
        msg["Description-Content-Type"] = contenttype
        msg.set_payload(content)

    # License must be a table with either a "text" or a "file" key. Either the text
    # string or the file's contents are added under the License core metadata field.
    # If I'm interpreting the spec right, the entire license is stuffed into this single
    # field. I wonder if the spec intended to e.g. include the entire GPL here?
    # I think the intent was to only use this field if the license is something
    # non-standard. Otherwise, use the appropriate classifier.
    if "license" in metadata:
        filename = metadata["license"].get("file")
        content = metadata["license"].get("text")
        if filename and content:
            raise UserError(
                f'"file" and "text" keys are mutually exclusive in {pyproject.file} project.license table'
            )
        if filename:
            content = open(filename, "r", encoding="utf-8").read()
            sources.append(filename)
        msg["License"] = content

    if "authors" in metadata:
        _write_contacts(msg, "Author", "Author-Email", metadata["authors"])
    if "maintainers" in metadata:
        _write_contacts(
            msg, "Maintainer", "Maintainer-Email", metadata["maintainers"]
        )

    if "keywords" in metadata:
        msg["Keywords"] = ",".join(metadata["keywords"])

    if "classifiers" in metadata:
        for c in metadata["classifiers"]:
            msg["Classifier"] = c

    if "urls" in metadata:
        for label, url in metadata["urls"].items():
            msg["Project-URL"] = f"{label}, {url}"

    if "dependencies" in metadata:
        for dep in metadata["dependencies"]:
            # Validate and normalize
            dep = str(packaging.requirements.Requirement(dep))
            msg["Requires-Dist"] = dep

    if "optional-dependencies" in metadata:
        for extra_name, dependencies in metadata["optional-dependencies"].items():
            if not EXTRA_RE.match(extra_name):
                raise UserError(f'Invalid extra name "{extra_name}"')
            msg["Provides-Extra"] = extra_name
            for dep in dependencies:
                # Validate and normalize
                dep = str(packaging.requirements.Requirement(dep))
                msg["Requires-Dist"] = f"{dep}; extra = '{extra_name}'"

    return str(msg), sources


class Wheel:
    """Represents a wheel being built

    Add an alias or refer to it in other builders using ``Wheel.target``
    """

    def __init__(
        self,
        env: Environment,
        tag: str,
        root_is_purelib: Optional[bool] = None,
        build_num: Optional[int] = None,
    ):
        self.env = env

        # Wheel configuration
        self.tag = tag
        self.tags = packaging.tags.parse_tag(tag)
        self.build_dir: Dir = env.Dir(env["WHEEL_BUILD_DIR"])
        self.build_num = build_num
        if root_is_purelib is None:
            root_is_purelib = tag.endswith("-none-any")
        self.root_is_purelib: bool = root_is_purelib

        # Env configuration
        self.wheel_output_dir = env.Dir(env.get("WHEEL_DIR"))

        platform_specifier = f"{sysconfig.get_platform()}-{sys.implementation.cache_tag}"

        # Derived configuration: temporary build directories
        self.wheel_build_dir: Dir = self.build_dir.Dir("wheel")
        self.build_temp_dir: Dir = self.build_dir.Dir(f"temp.{platform_specifier}")
        self.build_lib_dir: Dir = self.build_dir.Dir(f"lib.{platform_specifier}")

        # Read in previously parsed metadata
        self.pyproject: PyProject = env["PYPROJECT"]
        self.project_metadata = self.pyproject.project_metadata
        self.tool_metadata = self.pyproject.tool_metadata
        self.name = self.pyproject.name
        self.normalized_filename = self.pyproject.dist_filename
        self.version = self.pyproject.version

        wheel_filename = make_wheelname(
            self.normalized_filename,
            self.version,
            self.tag,
        )
        self.wheel_file = self.wheel_output_dir.File(wheel_filename)

        data_dir_name = f"{self.normalized_filename}-{self.version}.dist-info"
        self.wheel_data_dir = self.wheel_build_dir.Dir(data_dir_name)

        metadata_targets = []

        # Metadata and wheel metadata are built at construction time because we don't know
        # which sources to pass to SCons for dependency tracking until after the metadata
        # is read and parsed.
        metadata, metadata_sources = env["CORE_METADATA"], env["CORE_METADATA_SOURCES"]
        metadata_targets.extend(
            env.Command(
                self.wheel_data_dir.File("METADATA"),
                metadata_sources,
                _generate_str_writer_action(metadata),
            )
        )

        wheel_metadata, wheel_metadata_sources = self._get_wheel_metadata()
        metadata_targets.extend(
            env.Command(
                self.wheel_data_dir.File("WHEEL"),
                wheel_metadata_sources,
                _generate_str_writer_action(wheel_metadata),
            )
        )

        metadata_targets.append(
            env.Command(
                self.wheel_data_dir.File("entry_points.txt"),
                self.pyproject.file,
                self._build_entry_points,
            )
        )

        self._zip_env = env.Clone(ZIPROOT=self.wheel_build_dir)
        self.target = self._add_zip_sources(metadata_targets)

        env.AddPostAction(self.target, env.Action(self._add_manifest))
        env.Clean(self.target, self.wheel_build_dir)

    def _add_zip_sources(self, sources):
        return self._zip_env.Zip(
            self.wheel_file,
            sources,
        )

    def add_sources(self, sources, root="."):
        """Add sources to the wheel using the given root directory as the relative path root

        The root parameter controls how to map paths on the filesystem to paths in the wheel.
        For example:
            wheel.add_sources("src/packagename/modulename.py", "src")
        will zip the file into the wheel at "packagename/modulename.py.

        Similarly, if your packages or modules are in the top level directory of your
        repository (same directory as the SConstruct file):
            wheel.add_sources("packagename/modulename.py", ".")
        will add that file into the same place in the zip.

        For generated files, such as compiled extension modules, they are expected to
        live under one of the directories in ./build/, e.g. build/lib.linux-x86_64/modulename.so

        For example, these examples will add the extension module to the same place in the wheel:
            wheel.add_sources("build/lib.linux-x86_64/modulename.so", ".")
            wheel.add_sources("build/lib.linux-x86_64/src/modulename.so", "src")
        Both get added to the root of the wheel.

        More formally, the specified root is relative to the first match of:
        A) One of the subdirectories under build/
        B) The top level directory (next to the SConstruct file)

        All directories between the root and the source file are preserved in the wheel file.

        """
        sources = self.env.arg2nodes(sources, self.env.Entry)

        source: Entry
        for source in self.env.arg2nodes(sources, self.env.Entry):
            rel_path = get_rel_path(self.env, source)
            rel_path = os.path.relpath(rel_path, root)
            install_path = self.wheel_build_dir.Entry(rel_path)
            targets = self.env.InstallAs(install_path, source)
            self._add_zip_sources(targets)

    def add_data(self, category, sources, root="."):
        """Add sources to the data directory called "category", relative to the given root"""
        for source in self.env.arg2nodes(sources, self.env.Entry):
            rel_path = get_rel_path(self.env, source)
            rel_path = os.path.relpath(rel_path, root)
            install_path = self.wheel_data_dir.Dir(category).Entry(rel_path)
            targets = self.env.InstallAs(install_path, source)
            self._add_zip_sources(targets)

    def _add_manifest(self, target, source, env):
        # Called after the zip file has been written to the filesystem.
        archive = zipfile.ZipFile(
            target[0].get_path(), "a", compression=zipfile.ZIP_DEFLATED
        )
        lines = []
        for f in archive.namelist():
            data = archive.read(f)
            size = len(data)
            digest = hashlib.sha256(data).digest()
            digest = "sha256=" + (urlsafe_b64encode(digest).decode("ascii"))
            lines.append("%s,%s,%s" % (f.replace(",", ",,"), digest, size))

        record_path = os.path.join(self.wheel_data_dir.name, "RECORD")
        lines.append(record_path + ",,")
        RECORD = "\n".join(lines)
        with archive.open(record_path, "w") as f:
            f.write(RECORD.encode("utf-8"))
        archive.close()

    def _get_wheel_metadata(self) -> Tuple[str, Sequence[Union[str, "Node"]]]:
        msg = Message()
        msg["Wheel-Version"] = "1.0"
        msg["Generator"] = "enscons"
        msg["Root-Is-Purelib"] = str(self.root_is_purelib).lower()
        if self.build_num is not None:
            msg["Build"] = self.build_num
        for tag in self.tags:
            msg["Tag"] = str(tag)

        sources = [self.pyproject.file]
        if self.build_num is not None:
            sources.append(self.env.Value(f"Build: {self.build_num}"))

        return str(msg), sources

    def _build_entry_points(self, target, source, env):
        metadata = self.project_metadata

        groups = {}

        if "scripts" in metadata:
            groups["console_scripts"] = metadata["scripts"]

        if "gui-scripts" in metadata:
            groups["gui_scripts"] = metadata["gui-scripts"]

        if "entry-points" in metadata:
            for group, items in metadata["entry-points"].items():
                if group in ("scripts", "gui-scripts"):
                    raise UserError(
                        f"Invalid {self.pyproject} table "
                        f"project.entry-points.{group} Use project.{group} "
                        f"instead"
                    )
                groups[group] = items

        ini = ConfigParser()
        for group, items in groups.items():
            ini.add_section(group)
            for key, val in items.items():
                ini[group][key] = val

        with open(target[0].get_abspath(), "w", encoding="utf-8") as f:
            ini.write(f)


def SDist(env: Environment, sources) -> List["Entry"]:
    sources = env.arg2nodes(sources, env.Entry)
    build_dir = env["WHEEL_BUILD_DIR"].Dir("sdist")
    # Source dists must contain a pyproject.toml file
    if env.File("pyproject.toml") not in sources:
        raise ValueError("Source dists must contain a pyproject.toml")

    targets = [
        env.Command(
            get_build_path(env, "PKG-INFO", build_dir),
            env["CORE_METADATA_SOURCES"],
            _generate_str_writer_action(env["CORE_METADATA"]),
        )
    ]
    for source in sources:
        targets.extend(env.InstallAs(
            get_build_path(env, source, build_dir),
            source,
        ))

    pyproject: PyProject = env["PYPROJECT"]
    dirname = f"{pyproject.dist_filename}-{pyproject.version}"
    filename = f"{dirname}.tar.gz"
    target = env.PyTar(
        env["SDIST_DIR"].File(filename),
        targets,
        TARROOT=build_dir,
        TARPREFIX=dirname,
    )
    env.Clean(target, build_dir)
    return target


def Editable(env, src_root="."):
    """Returns a wheel built for installing an editable path"""
    root = env.Dir(src_root)
    raise NotImplementedError  # TODO


def make_wheelname(dist_name, version, wheel_tag, build_tag=None):
    """Returns the wheel name for the given distribution name, version, wheel tag,
    and optional build tag.

    This implements the naming convention described at
    https://packaging.python.org/en/latest/specifications/binary-distribution-format/#file-name-convention
    """
    if build_tag:
        template = "{distribution}-{version}-{build_tag}-{wheel_tag}.whl"
    else:
        template = "{distribution}-{version}-{wheel_tag}.whl"
    return template.format(
        distribution=dist_name, version=version, wheel_tag=wheel_tag, build_tag=build_tag
    )


def _write_contacts(
    msg: Message, header_name: str, header_email: str, contacts: List[dict]
):
    # Reference https://packaging.python.org/en/latest/specifications/declaring-project-metadata/#authors-maintainers
    names = []
    emails = []
    for contact in contacts:
        name = contact.get("name")
        email = contact.get("email")
        if not name and not email:
            raise UserError(
                f'At least one of "name" or "email" must be specified for each author and maintainer'
            )
        elif name and not email:
            names.append(name)
        elif email and not name:
            emails.append(email)
        else:
            emails.append(f"{name} <{email}>")

    if names:
        msg[header_name] = ", ".join(names)
    if emails:
        msg[header_email] = ", ".join(emails)


def _generate_str_writer_action(
    s: str,
) -> Callable[[Sequence["Node"], Sequence["Node"], Environment], None]:
    """Returns an SCons action function which writes the given string to the target"""

    def action(target, source, env):
        target: File = target[0]

        with open(target.get_abspath(), "w") as f:
            f.write(s)

    return action


def generate(env: Environment, **kwargs):
    pytar.generate(env)
    if "WHEEL_BUILD_DIR" not in env:
        env["WHEEL_BUILD_DIR"] = env.Dir("#build/")

    if "WHEEL_DIR" not in env:
        env["WHEEL_DIR"] = env.Dir("#dist/")

    if "SDIST_DIR" not in env:
        env["SDIST_DIR"] = env.Dir("#dist/")

    pyproject_file = env.get("PYPROJECT_FILE", "pyproject.toml")
    env["PYPROJECT"] = parse_pyproject_toml(pyproject_file)
    env["CORE_METADATA"], env["CORE_METADATA_SOURCES"] = build_core_metadata(env["PYPROJECT"])

    env.AddMethod(get_rel_path)
    env.AddMethod(get_build_path)

    env.AddMethod(Wheel)
    env.AddMethod(SDist)
    env.AddMethod(Editable)


def exists(env):
    return True
