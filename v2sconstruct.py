from typing import Any

import enscons.v2

Environment: Any

env = Environment(
    tools=["default", "packaging", enscons.v2.generate]
)

tag = "py38-none-any"

wheel = env.Wheel(
    tag
)
wheel.add_sources("enscons/v2.py", ".")
env.Alias("bdist_wheel", wheel.target)

sdist = env.SDist([
    "pyproject.toml",
    "enscons/v2.py",
    "enscons/pytar.py",
])
env.Alias("sdist", sdist)

editable = env.Editable(tag)
env.Alias("editable", editable)
