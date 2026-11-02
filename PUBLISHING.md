# Publishing releases

The `Publish to PyPI` GitHub Actions workflow tests the installed package on
Python 3.10 and 3.13, builds the wheel and source distribution, and checks both
with `twine check --strict`. It can publish from `main` using PyPI Trusted
Publishing, without storing an API token.

For the first release, sign in at
[PyPI account publishing](https://pypi.org/manage/account/publishing/) and add a
**pending publisher** using these exact values:

| Field | Value |
| --- | --- |
| PyPI project name | `pyelectrochemreader` |
| GitHub owner | `kevinsmia1939` |
| Repository name | `PyElectrochemReader` |
| Workflow filename | `publish.yml` |
| Environment name | `pypi` |

The workflow lives at `.github/workflows/publish.yml`. PyPI creates the project
on its first successful upload and converts the pending publisher to a regular
publisher. See [PyPI's setup instructions](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/).

After configuring the publisher, open
[Publish to PyPI](https://github.com/kevinsmia1939/PyElectrochemReader/actions/workflows/publish.yml),
select **Run workflow**, select branch `main`, and enable **Publish version from
pyproject.toml to PyPI**. Leaving that option disabled runs tests and builds
release artifacts without uploading them.

For subsequent releases, update both `project.version` in `pyproject.toml` and
`__version__` in `src/pyelectrochemreader/__init__.py`, commit and push to `main`,
then run the same workflow. Published PyPI versions cannot be overwritten.
