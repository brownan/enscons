from SCons.Environment import Environment

import enscons.v2

env = Environment(tools=["default", "packaging", enscons.v2.generate])

wheel = env.Wheel(
    tag="py38-none-any",
    pyproject="pyproject.toml",
)

def package_files(srcdir): ...

# Adds all python files matching ./flatpackage/**/*.py
# to WHEELROOT/flatpackage/
wheel.add_sources(package_files("flatpackage"), ".")

# Adds all python files matching ./src/srcpackage/**/*.py
# to WHEELROOT/srcpackage/
wheel.add_sources(package_files("src/srcpackage"), "src")


# Adds a single module at the top level
# Installs to WHEELROOT/module.py
wheel.add_sources("./module.py")

# Adds a single module at the top level
# Installs to WHEELROOT/module2.py
wheel.add_sources("src/module2.py", root="src")

# Compiles to
# build/temp.linux-x86_64-cpython-38/flatpackage/extmodule.o
# build/temp.linux-x86_64-cpython-38/flatpackage/extra.o
#
# Links to
# build/lib.linux-x86_64-cpython-38/flatpackage/extmodule.so
#
# Installs to
# WHEELROOT/flatpackage/extmodule.so
wheel.add_sources(env.ExtensionModule(
    "flatpackage/extmodule.c",
    extra_sources=["flatpackage/extra.c"],
    libs=["..."],
    includes=["..."],
))

# Compiles to
# build/temp.linux-x86_64-cpython-38/src/srcpackage/extmodule.o
# build/temp.linux-x86_64-cpython-38/src/srcpackage/extra.o
#
# Links to
# build/lib.linux-x86_64-cpython-38/src/srcpackage/extmodule.so
#
# Installs to
# WHEELROOT/srcpackage/extmodule.so
wheel.add_sources(env.ExtensionModule(
    "src/srcpackage/extmodule.c",
    extra_sources=["src/srcpackage/extra.c"],
    libs=["..."],
    includes=["..."],
), root="src")

# Translates to
# build/cython/flatpackage/cythonmodule.c
#
# Compiles to
# build/temp.linux-x86_64-cpython-38/flatpackage/cythonmodule.o
#
# Links to
# build/lib.linux-x86_64-cpython-38/flatpackage/cythonmodule.so
#
# Installs to
# WHEELROOT/flatpackage/cythonmodule.so
wheel.add_sources(
    env.ExtensionModule(
        env.Cython(
            "flatpackage/cythonmodule.pyx"
        )
    )
)

# Translates to
# build/cython/src/srcpackage/cythonmodule.c
#
# Compiles to
# build/temp.linux-x86_64-cpython-38/src/srcpackage/cythonmodule.o
#
# Links to
# build/lib.linux-x86_64-cpython-38/src/srcpackage/cythonmodule.so
#
# Installs to
# WHEELROOT/srcpackage/cythonmodule.so
wheel.add_sources(
    env.ExtensionModule(
        env.Cython(
            "src/srcpackage/cythonmodule.pyx"
        )
    ),
    root="src"
)
