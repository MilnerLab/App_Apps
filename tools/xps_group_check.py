"""Report how each XPS group is declared, and whether the RGV can spin.

Continuous rotation (``GroupSpinParametersSet``) is only defined on a group declared
``SpindleAxis`` in the controller's ``system.ini``. On a ``SingleAxis`` group the XPS
rejects the command -- correctly, since a SingleAxis group carries travel limits and
"turn forever" has no valid interpretation inside them. That declaration is therefore
the single thing that decides whether Spin works, and it is not visible from the app.

Read-only. It opens a connection, prints the ``[GROUPS]`` table, and exits:

    python tools/xps_group_check.py --host 192.168.0.254

Pass ``--dump-ini`` to print the whole system.ini, which is what you would edit (see
``--help`` for where it lives and what the edit is).
"""
from __future__ import annotations

import argparse
import sys

from control_readout.newport_xps.controller import XPSController

#: The group the RGV100BL half-wave plate lives on. Matches Rgv100blWorker.
RGV_GROUP = "GROUP1"

_EDIT_HELP = """
To change it, on the XPS itself:

  1. Browse to the controller's web interface and open  /Admin/Config/system.ini
     (over FTP the same file is at  Config/system.ini  under the login's home).
  2. In the [GROUPS] section, move the group from the SingleAxis list to the
     SpindleAxis one:

         [GROUPS]
         SingleAxis = GROUP2, GROUP3
         SpindleAxis = GROUP1

  3. Reboot the controller. Group declarations are read once at boot; nothing short
     of a restart picks up the change.

Take a copy of system.ini first. A malformed [GROUPS] section leaves groups
uninitialised at boot, which takes out every axis on the controller, not just this one.

A SpindleAxis group has NO travel limits -- that is the point of it, and it is also the
risk. Anything that assumes the plate cannot rotate past a bound stops being true.
"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_EDIT_HELP)
    ap.add_argument("--host", required=True, help="XPS controller IP or hostname")
    ap.add_argument("--username", default="Administrator")
    ap.add_argument("--password", default="Administrator")
    ap.add_argument("--group", default=RGV_GROUP,
                    help=f"group to report on in detail (default {RGV_GROUP})")
    ap.add_argument("--dump-ini", action="store_true",
                    help="also print the whole system.ini as the controller has it")
    args = ap.parse_args(argv)

    controller = XPSController(host=args.host, username=args.username,
                               password=args.password)
    controller.connect()
    try:
        categories = controller.group_categories()
        if not categories:
            print("The controller reported no groups at all -- is it initialised?")
            return 2

        print(f"Groups declared on {args.host}:")
        width = max(len(name) for name in categories)
        for name, category in sorted(categories.items()):
            mark = " <-- spin works here" if category.lower() == "spindleaxis" else ""
            print(f"  {name:<{width}}  {category}{mark}")

        print()
        category = categories.get(args.group)
        if category is None:
            print(f"{args.group} is not declared on this controller. "
                  f"The RGV worker addresses it by that exact name, so spin cannot work "
                  f"until the name matches.")
            return 2
        if category.lower() == "spindleaxis":
            print(f"{args.group} is a SpindleAxis: continuous rotation is available.")
            print("If Spin still fails, the cause is downstream of this -- check the "
                  "error the panel now reports.")
            return 0

        print(f"{args.group} is declared {category}, NOT SpindleAxis.")
        print("This is why Spin is refused: GroupSpinParametersSet does not exist for "
              "this group type.")
        print(_EDIT_HELP)

        if args.dump_ini:
            print("--- system.ini ---")
            print(controller.xps.read_systemini_text()
                  if hasattr(controller.xps, "read_systemini_text")
                  else "(this newportxps build exposes no raw-text reader; "
                       "fetch Config/system.ini over FTP)")
        return 1
    finally:
        controller.disconnect()


if __name__ == "__main__":
    sys.exit(main())
