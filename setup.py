import setuptools

with open("README.md", "r") as fh:
    long_description = fh.read()

setuptools.setup(
    name="pathtemplater",
    version="1.0.0.dev4",
    author="Tet Woo Lee",
    author_email="developer@twlee.nz",
    description="Package for templating paths, useful helper package for Snakemake",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/twlee79/pathtemplater",
    packages=setuptools.find_packages(),
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: GNU General Public License v3 (GPLv3)",
        "Operating System :: OS Independent",
    ],
    data_files=[("", ["LICENSE.md"])],
)
