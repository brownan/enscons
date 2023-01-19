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
    # Get compiler and compiler options we need to build a python extension module
    (
        cc,
        cxx,
        cflags,
        ccshared,
        ldshared,
        libdir,
        ext_suffix,
    ) = sysconfig.get_config_vars(
        "CC",
        "CXX",
        "CFLAGS",
        "CCSHARED",
        "LDSHARED",
        "LIBDIR",
        "EXT_SUFFIX",
    )

    include_dirs = {
        sysconfig.get_path("include"),
        sysconfig.get_path("platinclude"),
    }

    # Include Virtualenv
    if sys.exec_prefix != sys.base_exec_prefix:
        include_dirs.add(os.path.join(sys.exec_prefix, "include"))

    # Platform library directories
    library_dirs = {
        sysconfig.get_path("stdlib"),
        sysconfig.get_path("platstdlib"),
    }

    # Set compilers and flags
    env["CC"] = cc
    env["CXX"] = cxx
    env["SHLINK"] = ldshared
    env.Prepend(
        CFLAGS=shlex.split(cflags),
        CPPPATH=list(include_dirs),
        LIBPATH=list(library_dirs),
    )
    env.Replace(
        SHCFLAGS=shlex.split(ccshared) + env["CFLAGS"],
    )

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
        obj = get_build_path(env, node, build_dir, "")
        objects.append(env.SharedObject(target=str(obj), source=node))

    so = get_build_path(env, modsource, lib_dir, "")
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
