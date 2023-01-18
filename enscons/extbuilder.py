import distutils.sysconfig
import shlex
import sysconfig
import os.path
import sys
from pathlib import Path
from typing import Sequence, Optional, TYPE_CHECKING

from SCons.Node.FS import File

from enscons.v2 import get_build_path, get_rel_path

if TYPE_CHECKING:
    from SCons.Node.FS import Dir, Entry, File
    from SCons.Node import Node

def configure_compiler_env(env):
    # Get various compiler options we need to build a python extension module
    # Mostly ported from distutils.sysconfig
    (
        cc,
        cxx,
        cflags,
        ccshared,
        ldshared,
        libdir,
        ext_suffix,
    ) = distutils.sysconfig.get_config_vars(
        "CC",
        "CXX",
        "CFLAGS",
        "CCSHARED",
        "LDSHARED",
        "LIBDIR",
        "EXT_SUFFIX",
    )

    include_dirs = []

    # Include Virtualenvs
    if sys.exec_prefix != sys.base_exec_prefix:
        include_dirs.append(os.path.join(sys.exec_prefix, "include"))

    # Platform include directories
    py_include = distutils.sysconfig.get_python_inc()
    plat_include = distutils.sysconfig.get_python_inc(plat_specific=1)
    include_dirs.extend(py_include.split(os.path.pathsep))
    if plat_include != py_include:
        include_dirs.extend(plat_include.split(os.path.pathsep))

    # Platform library directories
    library_dirs = []
    library_dirs.append(libdir)

    # Set compilers and flags
    env["CC"] = cc
    env["CXX"] = cxx
    env["SHLINK"] = ldshared
    env.Prepend(
        CFLAGS=shlex.split(cflags),
        CPPPATH=include_dirs,
        LIBPATH=library_dirs,
    )
    env.Replace(SHCFLAGS=shlex.split(ccshared) + env["CFLAGS"])

    # Naming convention for extension module shared objects
    env["SHLIBSUFFIX"] = ext_suffix
    env["SHLIBPREFIX"] = ""


def ExtModule(
    env,
    modsource: File,
    extra_sources: Optional[Sequence[File]] = None,
):
    """Compiles and adds an extension module to a wheel"""
    env = env.Clone()
    configure_compiler_env(env)

    platform_specifier = f"{sysconfig.get_platform()}-{sys.implementation.cache_tag}"
    build_dir: "Dir" = env["WHEEL_BUILD_DIR"].Dir(f"temp.{platform_specifier}")
    lib_dir: "Dir" = env["WHEEL_BUILD_DIR"].Dir(f"lib.{platform_specifier}")

    modsource = env.arg2nodes(modsource, env.File)[0]

    source_files = [modsource]

    if extra_sources:
        source_files.extend(env.arg2nodes(extra_sources, env.File))

    objects = []
    for node in source_files:
        obj = Path(get_build_path(env, node, build_dir).get_path()).with_suffix("")
        objects.append(env.SharedObject(target=str(obj), source=node))

    so = Path(get_build_path(env, modsource, lib_dir).get_path()).with_suffix("")
    library = env.SharedLibrary(target=str(so), source=objects)

    return library


def InstallExtensionInplace(
    env,
    ext_module: File,
):
    targets = []
    ext_modules = env.arg2nodes(ext_module, env.File)
    for module in ext_modules:
        relpath = get_rel_path(env, module)
        targets.extend(env.InstallAs(relpath, module))
    return targets

def generate(env, **kwargs):
    env.AddMethod(ExtModule)
    env.AddMethod(InstallExtensionInplace)


def exists(env):
    return True