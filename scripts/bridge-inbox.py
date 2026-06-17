#!/usr/bin/env python3
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bridge_inbox


if __name__ == "__main__":
    raise SystemExit(bridge_inbox.main())
