from setuptools import setup

from project_context import __version__

setup(
    name="project-context-cli",
    version=__version__,
    author="CalumRakk",
    author_email="leocasti2@gmail.com",
    description="A CLI tool for managing project context with Google AI Studio",
    packages=[
        "project_context",
        "project_context.commands",
        "project_context.ui",
    ],
    install_requires=[
        "typer==0.21.1",
        "gitingest @ git+https://github.com/CalumRakk/gitingest.git@fix/windows-encoding-support",
        "google-api-python-client==2.187.0",
        "google-auth-oauthlib==1.2.3",
        "GitPython==3.1.45",
    ],
    keywords="cli google-ai-studio project-context",
    include_package_data=True,
    entry_points={
        "console_scripts": [
            "project_context=project_context.main:main",
        ],
    },
)
