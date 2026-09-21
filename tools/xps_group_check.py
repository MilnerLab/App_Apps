"""Report how each XPS group is declared, and how it will be spun.

Spin works either way, but by two different mechanisms, and this says which one you are
on. A ``SpindleAxis`` group has the native spin commands. A ``SingleAxis`` group does
not -- ``GroupSpinParametersGet`` answers -18, "wrong object type" -- and is instead
spun by a very long move, which its travel limits make effectively continuous. This also
prints how long that lasts before the limit is reached, which is the one number the
SingleAxis path has and the SpindleAxis path does not.

Read-only: it opens a connection, reads, and exits without commanding any motion.

    python tools/xps_group_check.py --host 10.1.137.137 \
        --username PyControl --password labview2python
"""
from __future__ import annotations

import argparse
import sys

from control_readout.newport_xps.controller import XPSController

#: The group the RGV100BL half-wave plate lives on. Matches Rgv100blWorker.
RGV_GROUP = "GROUP1"

_EDIT_HELP = """
Spin does not require this change -- the SingleAxis path above already works. Convert the
group only if you want genuinely unbounded rotation and a seamless mid-spin rate change.
On the XPS itself:

  1. Browse to the controller's web interface and open  /Admin/Config/system.ini
     (over FTP the same file is at  Config/system.ini  under the login's home).
  2. In the [GROUPS] section, move the group from the SingleAxis list to the
     SpindleAxis one:

         [GROUPS]
         SingleAxis = GROUP2, GROUP3
         SpindleAxis = GROUP1

  3. Reboot the controller. Group declarations are read once at boot; nothing short
     of a restart picks up the change -- which is also why the type cannot be switched
     per-operation, only per-boot.

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
            print(f"{args.group} is a SpindleAxis: it spins natively, without a limit, "
                  f"and re-rates smoothly while turning.")
            return 0

        print(f"{args.group} is declared {category}, not SpindleAxis.")
        print("Spin still works: it is issued as a long move toward the travel limit, on "
              "a second connection so the blocking call does not tie up the main socket.")
        try:
            positioner = f"{args.group}.POSITIONER"
            err, low, high = controller.xps._xps.PositionerUserTravelLimitsGet(
                controller.xps._sid, positioner)
            # Positioners are addressed by their fully-qualified "GROUP.NAME", which is
            # what the device layer builds and what the XPS itself indexes them under.
            here = controller.get_position((args.group, positioner))
            if err == 0:
                print()
                print(f"  travel limits : {low:,.0f} to {high:,.0f} deg")
                print(f"  position now  : {here:,.1f} deg")
                for rev_s in (0.5, 2.0):
                    hours = abs(high - here) / (rev_s * 360.0) / 3600.0
                    print(f"  at {rev_s:>3} rev/s   : {hours:,.1f} hours "
                          f"of continuous rotation before the limit")
        except Exception as exc:            # diagnostics only -- never fail the check
            print(f"  (could not read travel limits: {exc})")
        print()
        print("The trade-off against a real SpindleAxis: a rate change mid-spin has to "
              "abort and re-issue the move, so the plate decelerates and ramps back up "
              "rather than sliding to the new rate.")
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
