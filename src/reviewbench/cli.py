import argparse


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="reviewbench", description="Run and compare AI code reviewers."
    )
    parser.parse_args(argv)
    parser.print_help()
    return 0
