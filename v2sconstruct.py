from typing import Any

import enscons.v2

Environment: Any

env = Environment(
    tools=["default", "packaging", enscons.v2.generate]
)

wheel = env.Wheel(
    tag="py38-none-any",
)
wheel.add_sources("enscons/v2.py", ".")
env.Alias("bdist_wheel", wheel.target)