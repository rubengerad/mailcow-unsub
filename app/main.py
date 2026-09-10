import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

import bootstrap  # noqa: E402

bootstrap.run()

import poller  # noqa: E402

poller.main()
