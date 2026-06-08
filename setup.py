from setuptools import setup, find_packages

with open("README.md", encoding="utf-8") as f:
    long_description = f.read()

with open("requirements.txt") as f:
    requirements = [
        line.strip()
        for line in f
        if line.strip() and not line.startswith("#")
    ]

setup(
    name="digihealth-lamp",
    version="1.0.0",
    description="Smart lamp per monitoraggio ambientale indoor con NeoPixel, IAQI e dashboard web",
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="digip",
    packages=find_packages(),
    python_requires=">=3.7",
    install_requires=requirements,
    entry_points={
        "console_scripts": [
            "digihealth-lamp=digihealth.main:main",
        ],
    },
    include_package_data=True,
    package_data={
        "digihealth": ["web/templates/*.html"],
        "": ["config/*.yaml", "audio/*.mp3"],
    },
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: POSIX :: Linux",
    ],
)
