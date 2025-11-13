from setuptools import setup

setup(
    name="project-context-cli",
    version="0.1.0",
    author="CalumRakk",
    author_email="leocasti2@gmail.com",
    description="A CLI tool for managing project context with Google AI Studio",
    packages=["project_context", "project_context.browser"],
    install_requires=[
        "click==8.3.0",
        "gitingest==0.3.1",
        "google-api-python-client==2.187.0",
        "selenium==4.38.0",
        "undetected-chromedriver==3.5.5",
    ],
    keywords="cli google-ai-studio project-context",
    include_package_data=True,
    entry_points={
        "console_scripts": [
            "project_context=project_context.cli:main",
        ],
    },
)
