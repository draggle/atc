"""One table: ICAO airline designator -> the name said on the radio (the telephony designator).

Real callsigns are an airline code plus a flight identifier, and the identifier is often
alphanumeric: BAW27G is "Speedbird two seven golf", RYR4JL is "Ryanair four juliett lima".
The telephony name is frequently not the airline's name (Speedbird, Shamrock, Channex).

This used to live in three places with three different contents (planner/cards.py,
tower/normalize.py, pilots/readback.py). Everything imports from here now.

Coverage was built against real traffic on 2026-09-18 (tools/real_build.py). Names are from
memory of the ICAO designator list and were not checked against Doc 8585 this weekend: good enough
for a simulator, verify before quoting one to an aviation judge. Codes not listed here are spelled
phonetically, which is also what a controller does with an unfamiliar operator.
"""
from __future__ import annotations

import re

ICAO_TO_TELEPHONY: dict[str, str] = {
    # Canada
    "ACA": "Air Canada", "WJA": "WestJet", "JZA": "Jazz", "POE": "Porter", "ROU": "Rouge",
    "TSC": "Transat", "SWG": "Sunwing", "FLE": "Flair", "CJT": "Cargojet", "WEN": "Encore",
    "MAL": "Morningstar",
    # United States
    "AAL": "American", "DAL": "Delta", "UAL": "United", "SWA": "Southwest", "JBU": "JetBlue",
    "ASA": "Alaska", "NKS": "Spirit Wings", "FFT": "Frontier Flight", "AAY": "Allegiant",
    "SCX": "Sun Country", "HAL": "Hawaiian", "SKW": "SkyWest", "RPA": "Brickyard", "ENY": "Envoy",
    "EDV": "Endeavor", "JIA": "Blue Streak", "PDT": "Piedmont", "ASH": "Air Shuttle",
    "GJS": "Lindbergh", "FDX": "FedEx", "UPS": "UPS", "GTI": "Giant", "ABX": "Abex",
    "ATN": "Air Transport", "CKS": "Connie", "EJA": "ExecJet", "LXJ": "Flexjet",
    # United Kingdom and Ireland
    "BAW": "Speedbird", "SHT": "Shuttle", "EFW": "Griffin", "VIR": "Virgin", "EZY": "Easy",
    "EXS": "Channex", "TOM": "Tomjet", "LOG": "Logan", "EIN": "Shamrock", "RYR": "Ryanair",
    "RUK": "Bluemax", "WUK": "Wizz Go", "DHK": "World Express",
    # Western Europe
    "DLH": "Lufthansa", "GEC": "Lufthansa Cargo", "EWG": "Eurowings", "CFG": "Condor",
    "TUI": "Tui Jet", "BOX": "German Cargo", "BCS": "Eurotrans", "AFR": "Air France",
    "TVF": "France Soleil", "KLM": "KLM", "TRA": "Transavia", "MPH": "Martinair",
    "BEL": "Beeline", "JAF": "Beauty", "LGL": "Luxair", "CLX": "Cargolux", "SWR": "Swiss",
    "EDW": "Edelweiss", "EZS": "Topswiss", "EJU": "Alpine", "AUA": "Austrian", "IBE": "Iberia",
    "IBS": "Iberexpres", "VLG": "Vueling", "AEA": "Europa", "VOE": "Volotea", "TAP": "Air Portugal",
    "ITY": "Itarrow", "NJE": "Fraction", "VJT": "Vista Malta",
    # Nordics and Baltics
    "SAS": "Scandinavian", "NAX": "Nor Shuttle", "NOZ": "Nordic", "NSZ": "Rednose", "FIN": "Finnair",
    "ICE": "Iceair", "BTI": "Air Baltic", "VKG": "Viking",
    # Central and Eastern Europe
    "LOT": "LOT", "ENT": "Enter", "WZZ": "Wizzair", "WMT": "Wizzair Malta", "CSA": "CSA",
    "TVS": "Skytravel", "ROT": "Tarom", "AEE": "Aegean", "THY": "Turkish", "PGT": "Sunturk",
    "SXS": "Sunexpress", "CTN": "Croatia", "ASL": "Air Serbia", "LZB": "Flying Bulgaria",
    # Middle East, Africa, Asia-Pacific, Latin America
    "UAE": "Emirates", "ETD": "Etihad", "QTR": "Qatari", "SVA": "Saudia", "ELY": "El Al",
    "RJA": "Jordanian", "MSR": "Egyptair", "RAM": "Royalair Maroc", "ETH": "Ethiopian",
    "KQA": "Kenya", "AIC": "Air India", "IGO": "Ifly", "SIA": "Singapore", "CPA": "Cathay",
    "CCA": "Air China", "CES": "China Eastern", "CSN": "China Southern", "CAL": "Dynasty",
    "EVA": "Eva", "JAL": "Japan Air", "ANA": "All Nippon", "KAL": "Korean Air", "AAR": "Asiana",
    "QFA": "Qantas", "ANZ": "New Zealand", "AMX": "Aeromexico", "TAM": "Tam", "AVA": "Avianca",
    "CMP": "Copa",
    # seen in the 2026-09-18 traffic: smaller operators, state aircraft, a few long-haul carriers
    "RCH": "Reach", "MXY": "Moxy", "CAI": "Corendon", "GFA": "Gulf Air",
    "OMA": "Oman Air", "ABY": "Arabia", "THT": "Tahiti Airlines", "CXA": "Xiamen Air",
    "KLC": "City", "EJM": "Jet Speed", "SEH": "Sky Express", "GAF": "German Air Force",
    "CMB": "Camber",
}

# Spoken name -> code, for the normalizer. Extra spellings a speech model tends to produce.
TELEPHONY_TO_ICAO: dict[str, str] = {name.lower(): code for code, name in ICAO_TO_TELEPHONY.items()}
TELEPHONY_TO_ICAO.update({
    "west jet": "WJA", "easyjet": "EZY", "easy jet": "EZY", "speed bird": "BAW", "ryan air": "RYR",
    "wizz air": "WZZ", "wizz": "WZZ", "jet blue": "JBU", "euro wings": "EWG", "fedex": "FDX",
    "fed ex": "FDX", "air portugal": "TAP", "tom jet": "TOM", "k l m": "KLM", "c s a": "CSA",
    "u p s": "UPS", "sky west": "SKW", "sun express": "SXS", "ice air": "ICE",
})

# What counts as an airline flight in real traffic: a three-letter ICAO designator, then a flight
# identifier that starts with a digit (BAW27G, RYR4JL). Registrations used as callsigns (GABCD,
# N123AB) and most military and private traffic do not match. Used by the archive extractor and by
# live mode, so both keep the same flights.
AIRLINE_CALLSIGN = re.compile(r"^[A-Z]{3}[0-9][A-Z0-9]{0,3}$")


def is_airline_callsign(callsign: str | None) -> bool:
    return bool(callsign) and AIRLINE_CALLSIGN.match(callsign) is not None


__all__ = ["AIRLINE_CALLSIGN", "ICAO_TO_TELEPHONY", "TELEPHONY_TO_ICAO", "is_airline_callsign"]
