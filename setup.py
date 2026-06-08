from setuptools import setup, find_packages

setup(
    name="kube-mind",
    version="0.1.0",
    packages=find_packages(),
    install_requires=[
        "typer[all]",
        "rich",
        "openai",
        "langgraph",
        "kubernetes",
        "google-cloud-container",
        "nemoguardrails",
        "python-dotenv",
    ],
    entry_points={
        "console_scripts": [
            "kube-mind=kube_mind.cli:cli",
        ],
    },
    python_requires=">=3.10",
)
