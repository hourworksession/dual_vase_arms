"""Allow ``python -m dual_arm_printer ...`` to invoke the CLI."""
from .cli.main import cli

if __name__ == "__main__":  # pragma: no cover
    cli()
