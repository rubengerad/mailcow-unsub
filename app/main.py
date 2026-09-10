import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

from version import __version__  # noqa: E402

logging.getLogger("mailcow-unsub").info("mailcow-unsub v%s starting", __version__)

import bootstrap  # noqa: E402

bootstrap.run()

import poller  # noqa: E402

poller.main()
