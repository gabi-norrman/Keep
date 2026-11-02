"""One folder per outside service. The directories config.yaml's `extra_integration_dirs` lists
join this package's path, so an install's own integration folders are found as these are
(common/IntegrationDirs.py)."""
from common.IntegrationDirs import extra_roots

__path__ += [str(root) for root in extra_roots()]
