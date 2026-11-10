import subprocess


def calculate(expression: str):
    return eval(expression)


def ping(host: str):
    return subprocess.run(f"ping -c 1 {host}", shell=True, capture_output=True)
