"""Package setup — installs the `silicon-ring` CLI command."""
from setuptools import setup, find_packages

setup(
    name="silicon-ring",
    version="0.1.0",
    description="Silicon Ring — central voice call routing server and CLI.",
    packages=find_packages(),
    install_requires=[
        "click>=8.1",
        "httpx>=0.27",
        "tomllib; python_version < '3.11'",
    ],
    entry_points={
        "console_scripts": [
            "silicon-ring=ring_cli.cli:main",
        ],
    },
    python_requires=">=3.11",
)
