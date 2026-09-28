# SPDX-License-Identifier: LGPL-3.0-or-later
"""Length and nominal-size text for isometric drawings (pure Python)."""

from math import gcd

FRAC_DEN = 16  # lengths to the nearest 1/16 in

# Nominal bore in inches for each DN (ASME B36.10 / ISO 6708).  Kept here, not
# taken from quetzal_units, so this module stays importable without FreeCAD.
DN_INCH = {
    6: 0.125, 8: 0.25, 10: 0.375, 15: 0.5, 20: 0.75, 25: 1, 32: 1.25, 40: 1.5,
    50: 2, 65: 2.5, 80: 3, 90: 3.5, 100: 4, 125: 5, 150: 6, 200: 8, 250: 10,
    300: 12, 350: 14, 400: 16, 450: 18, 500: 20, 550: 22, 600: 24, 650: 26,
    700: 28, 750: 30, 800: 32, 850: 34, 900: 36, 1000: 40, 1050: 42, 1200: 48,
}


def ftin(mm, den=FRAC_DEN):
    """Feet-inch-fraction text, rounded to the nearest 1/den in:
    4036.65 mm -> 13'-2 15/16", 326.8 mm -> 1'-0 7/8", 300.45 mm -> 11 13/16".

    FreeCAD's 'Building US' schema truncates to the fraction and joins it with
    a '+', so the text is formatted here.  (From the AI_Piping_Design
    TechDraw examples, MIT licence, same author.)"""
    n = int(round(mm / 25.4 * den))
    ft, rem = divmod(n, 12 * den)
    whole, frac = divmod(rem, den)
    fs = ""
    if frac:
        g = gcd(frac, den)
        fs = "%d/%d" % (frac // g, den // g)
    if fs:
        ins = "%d %s" % (whole, fs) if (whole or ft) else fs
    else:
        ins = str(whole)
    return ("%d'-%s\"" % (ft, ins)) if ft else ("%s\"" % ins)


def millimetres(mm):
    return "%d" % int(round(mm))


def length_text(mm, units):
    """units: 'ftin' or 'mm'."""
    return ftin(mm) if units == "ftin" else millimetres(mm)


def inch_fraction(value):
    """1.25 -> '1-1/4', 0.5 -> '1/2', 2 -> '2'."""
    whole = int(value + 1e-9)
    rest = value - whole
    if rest < 1e-6:
        return str(whole)
    n = int(round(rest * 16))
    g = gcd(n, 16)
    frac = "%d/%d" % (n // g, 16 // g)
    return "%d-%s" % (whole, frac) if whole else frac


def size_text(bore, units_bore, system):
    """Nominal size of a PCF bore for display.

    system: 'NPS' -> 2", 'DN' -> DN50.  Converts between bore units as needed.
    """
    if bore <= 0:
        return ""
    if units_bore.upper().startswith("IN"):
        inches = bore
        dn = min(DN_INCH, key=lambda d: abs(DN_INCH[d] - bore))
    else:
        dn = min(DN_INCH, key=lambda d: abs(d - bore))
        inches = DN_INCH[dn]
    if system == "NPS":
        return inch_fraction(inches) + '"'
    return "DN%d" % dn
