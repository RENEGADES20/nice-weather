"""Isolated Nautilus workbench. Importing this package never loads live credentials."""

ENGINE_VERSION = "1.231.0"
STRATEGIES = {
    "noop": {
        "version": "1",
        "modes": ["sandbox", "backtest", "live"],
        "parameters": {},
        "requires": ["quote"],
        "description": "Acceptance: no orders",
    },
    "acceptance_roundtrip": {
        "version": "1",
        "modes": ["sandbox", "backtest"],
        "requires": ["quote"],
        "parameters": {"quantity": 1.0, "exit_after_quotes": 3, "require_both": False},
        "description": "Acceptance only: deterministic buy / exit; no claimed trading edge",
    },
}

for _name in ("S1", "S2", "S3", "S1_S2_S3"):
    STRATEGIES[_name] = {
        "version": "knyc-executable-v1",
        "modes": ["sandbox", "backtest"],
        "parameters": {},
        "requires": ["weather_signal", "depth", "contract"],
        "description": "Frozen KNYC weather strategy; validated station model required",
    }


def validate_strategy(strategy_id: str, parameters: dict, mode: str) -> dict:
    import math

    if strategy_id not in STRATEGIES or mode not in STRATEGIES[strategy_id]["modes"]:
        raise ValueError("Unregistered strategy or forbidden mode")
    defaults = STRATEGIES[strategy_id]["parameters"]
    if set(parameters) - set(defaults):
        raise ValueError("Unknown strategy parameter")
    result = defaults | parameters
    for key, value in result.items():
        if key == "require_both":
            if type(value) is not bool:
                raise ValueError("require_both must be boolean")
        elif key == "exit_after_quotes":
            if type(value) is not int or not 2 <= value <= 100000:
                raise ValueError("exit_after_quotes must be an integer in [2, 100000]")
        elif type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 100:
            raise ValueError(f"Invalid {key}")
    return result
