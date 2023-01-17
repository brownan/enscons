import distutils.sysconfig
import shlex
import sysconfig
import os.path
import sys
from pathlib import Path
from typing import Sequence, Optional

from SCons.Node.FS import File


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
    root: Optional[str] = None,
):
    """Compiles and adds an extension module to a wheel"""
    root = root or env["PY_SOURCE_ROOT"]
    env = env.Clone()
    configure_compiler_env(env)

    modsource = env.arg2nodes(modsource, env.File)[0]

    source_files = [modsource]

    if extra_sources:
        source_files.extend(env.arg2nodes(extra_sources, env.File))

    objects = []
    for node in source_files:
        obj_file_name = os.path.relpath(node.get_path(), root)
        target = Path(env["PY_TEMP_DIR"], obj_file_name).with_suffix("")
        objects.append(env.SharedObject(target=str(target), source=node))

    relpath = os.path.dirname(os.path.relpath(modsource.get_path(), root))
    module_name = Path(modsource.get_path()).stem
    module_target = os.path.join(env["PY_LIB_DIR"], relpath, module_name)
    library = env.SharedLibrary(target=str(module_target), source=objects)
    return library


def InstallExtensionInplace(
    env,
    ext_module: File,
    root: Optional[str] = None,
):
    ext_module = env.arg2nodes(ext_module, env.File)[0]
    root = root or env["PY_SOURCE_ROOT"]
    relpath = os.path.dirname(os.path.relpath(ext_module.get_path(), env["PY_LIB_DIR"]))
    directory = os.path.join(root, relpath)

    return env.Install(directory, ext_module)


def WhlExtension(
    env,
    ext_module: File,
):
    ext_module = env.arg2nodes(ext_module, env.File)[0]
    relpath = os.path.dirname(os.path.relpath(ext_module.get_path(), env["PY_LIB_DIR"]))

    directory = env["WHEEL_PATH"].Dir(relpath)
    return env.Install(directory, ext_module)

def generate(env, **kwargs):
    env.AddMethod(ExtModule)
    env.AddMethod(WhlExtension)
    env.AddMethod(InstallExtensionInplace)

    # Add some construction env vars that shouldn't affect non-python compilations
    # Set build paths for objects and shared libraries
    plat_name = sysconfig.get_platform()
    plat_specifier = f".{plat_name}-{sys.implementation.cache_tag}"
    env["PY_TEMP_DIR"] = f"build/temp{plat_specifier}"
    env["PY_LIB_DIR"] = f"build/lib{plat_specifier}"


def exists(env):
    return True