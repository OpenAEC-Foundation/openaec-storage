"""Aanwezigheid (ontwerp §5.8): wie heeft een bestand nu open, en bekijkt of bewerkt hij het.

Vluchtige data: alleen in Redis (`frappe.cache`), nooit in de database. Een client meldt zich bij het openen met
`meld_aanwezig` en herhaalt dat elke 30 seconden met de `sessie` uit het antwoord. Na 90 seconden zonder melding
vervalt de sessie. Bij elke wijziging van de lijst gaat `bestand.aanwezigheid` naar de kamer van het bestand. Uitleg en
keuzes: docs/samenwerken/aanwezigheid.md.
"""

import json
import time
from datetime import UTC, datetime

import frappe
from frappe import _
from frappe.realtime import get_doc_room
from frappe.utils import convert_utc_to_system_timezone, cstr, get_fullname

from openaec_storage.api import _bestand
from openaec_storage.rechten import weiger

VERVALT = 90  # seconden zonder melding: dan vervalt een sessie
BEWAAR = 3600  # seconden: zo lang blijft de stand van een bestand in Redis na de laatste aanroep
MAX_SESSIES = 10  # open sessies per gebruiker per bestand
MAX_LABEL = 140  # tekens van `app` en `apparaat`
MODI = {"bekijken": "read", "bewerken": "write"}
GEBEURTENIS = "bestand.aanwezigheid"
SLEUTEL = "openaec_storage:aanwezigheid:"  # + bestand: hash met een veld per sessie, plus '#stand'
BESTANDEN = "openaec_storage:aanwezigheid_bestanden"  # set van de bestanden met aanwezigen (voor de scheduler)

# Eén atomaire stap op de sessies van één bestand. Redis voert een script in zijn geheel uit, zonder ander werk
# ertussen: twee meldingen tegelijk overschrijven elkaar dus niet, en elke lijst hoort bij precies één stand.
# Eerst gaan de verlopen sessies eruit; daarna de actie: `meld` (nieuw of herhalen), `weg` of `ruim` (alleen opruimen).
# Een sessie van een andere gebruiker telt als onbekend. Bij elke wijziging gaat de stand omhoog: de tijd in
# milliseconden, en altijd hoger dan de vorige stand.
_BIJWERKEN = """
local h, bestanden = KEYS[1], KEYS[2]
local bestand, nu, vervalt = ARGV[1], tonumber(ARGV[2]), tonumber(ARGV[3])
local bewaar, max_sessies = tonumber(ARGV[4]), tonumber(ARGV[5])
local actie, gebruiker, sessie, nieuw = ARGV[6], ARGV[7], ARGV[8], ARGV[9]
local veranderd, gebruikt, sessies = 0, '', {}

local velden = redis.call('HGETALL', h)
for i = 1, #velden, 2 do
	if velden[i] ~= '#stand' then
		local r = cjson.decode(velden[i + 1])
		if nu - r.laatst >= vervalt then
			redis.call('HDEL', h, velden[i])
			veranderd = 1
		else
			sessies[velden[i]] = r
		end
	end
end

local eigen = sessies[sessie]
if eigen and eigen.gebruiker ~= gebruiker then
	eigen = nil
end

if actie == 'meld' then
	local r = cjson.decode(ARGV[10])
	r.gebruiker, r.laatst = gebruiker, nu
	if eigen then
		gebruikt, r.sinds = sessie, eigen.sinds
		if r.modus ~= eigen.modus or r.app ~= eigen.app or r.apparaat ~= eigen.apparaat then
			veranderd = 1
		end
	else
		local aantal = 0
		for _, s in pairs(sessies) do
			if s.gebruiker == gebruiker then
				aantal = aantal + 1
			end
		end
		if aantal < max_sessies then
			gebruikt, r.sinds, veranderd = nieuw, nu, 1
		end
	end
	if gebruikt ~= '' then
		redis.call('HSET', h, gebruikt, cjson.encode(r))
		sessies[gebruikt] = r
	end
elseif actie == 'weg' and eigen then
	redis.call('HDEL', h, sessie)
	sessies[sessie] = nil
	veranderd = 1
end

local stand = tonumber(redis.call('HGET', h, '#stand') or 0)
if veranderd == 1 then
	stand = math.max(math.floor(nu * 1000), stand + 1)
	redis.call('HSET', h, '#stand', stand)
end
local lijst = {}
for _, r in pairs(sessies) do
	lijst[#lijst + 1] = cjson.encode(r)
end
if #lijst > 0 then
	redis.call('SADD', bestanden, bestand)
else
	redis.call('SREM', bestanden, bestand)
end
if stand > 0 then
	redis.call('EXPIRE', h, bewaar)
end
return {gebruikt, veranderd, stand, lijst}
"""


def _nu() -> float:
	"""De tijd in seconden. Tests zetten de tijd hier, zodat ze niet echt hoeven te wachten."""
	return time.time()


def _tijd(seconden: float) -> datetime:
	"""Seconden sinds 1970 als tijd in de tijdzone van het systeem, zoals de rest van de API (`now_datetime`)."""
	return convert_utc_to_system_timezone(datetime.fromtimestamp(seconden, UTC)).replace(tzinfo=None)


def _label(waarde) -> str | None:
	"""`app` of `apparaat`: een korte naam die de client zelf kiest, ingekort tot MAX_LABEL tekens."""
	return cstr(waarde).strip()[:MAX_LABEL] or None


def _werk_bij(bestand: str, actie: str, sessie: str | None = None, record: dict | None = None) -> tuple[str, dict]:
	"""Voer één stap uit op de sessies van het bestand (zie `_BIJWERKEN`) en push de lijst als die veranderde. Geeft
	de gebruikte sessie (leeg als er geen nieuwe bij mocht) en het bericht `{bestand, stand, aanwezigen}`."""
	nieuw = f"{bestand}:{frappe.generate_hash(length=20)}" if actie == "meld" else ""
	gebruikt, veranderd, stand, lijst = frappe.cache.register_script(_BIJWERKEN)(
		keys=[frappe.cache.make_key(SLEUTEL + bestand), frappe.cache.make_key(BESTANDEN)],
		args=[
			bestand,
			round(_nu(), 3),
			VERVALT,
			BEWAAR,
			MAX_SESSIES,
			actie,
			frappe.session.user,
			sessie or "",
			nieuw,
			json.dumps(record or {}),
		],
	)
	sessies = sorted((json.loads(r) for r in lijst), key=lambda r: (r["sinds"], r["gebruiker"]))
	bericht = {
		"bestand": bestand,
		"stand": stand,
		"aanwezigen": [
			{
				"gebruiker": r["gebruiker"],
				"naam": get_fullname(r["gebruiker"]),
				"modus": r["modus"],
				"app": r["app"],
				"apparaat": r["apparaat"],
				"sinds": _tijd(r["sinds"]),
			}
			for r in sessies
		],
	}
	if veranderd:
		# De kamer staat er expliciet bij: zonder `room` stuurt Frappe binnen een taak naar de kamer van die taak.
		frappe.publish_realtime(GEBEURTENIS, bericht, room=get_doc_room("Opslagbestand", bestand))
	return cstr(gebruikt), bericht


@frappe.whitelist(methods=["POST"])
def meld_aanwezig(
	bestand: str, modus: str, app: str | None = None, apparaat: str | None = None, sessie: str | None = None
) -> dict:
	"""Ik heb dit bestand open om te `bekijken` (leesrecht) of te `bewerken` (schrijfrecht). Herhaal elke 30 s met de
	`sessie` uit het antwoord; na 90 s zonder melding vervalt ze. Een onbekende, verlopen of andermans sessie geeft een
	nieuwe. Mag je hier (niet meer), dan volgt een weigering en verdwijnt je sessie. Antwoord: `{sessie, bestand,
	stand, aanwezigen}`."""
	if modus not in MODI:
		weiger(_("Kies bekijken of bewerken"), "onbekende_modus")
	try:
		_bestand(bestand, MODI[modus])
	except (frappe.PermissionError, frappe.DoesNotExistError):
		if sessie:
			_werk_bij(bestand, "weg", sessie)
		raise
	record = {"modus": modus, "app": _label(app), "apparaat": _label(apparaat)}
	gebruikt, bericht = _werk_bij(bestand, "meld", sessie, record)
	if not gebruikt:
		weiger(
			_("Je hebt dit bestand al op {0} plekken open").format(MAX_SESSIES), "te_veel_sessies", frappe.PermissionError
		)
	return {"sessie": gebruikt, **bericht}


@frappe.whitelist(methods=["POST"])
def meld_weg(sessie: str) -> None:
	"""Ik sluit het bestand. Alleen een eigen sessie verdwijnt; een onbekende, verlopen of andermans sessie doet niets."""
	bestand = cstr(sessie).rpartition(":")[0]
	if bestand:
		_werk_bij(bestand, "weg", sessie)


@frappe.whitelist()
def aanwezig(bestand: str) -> dict:
	"""Wie heeft dit bestand nu open: `{bestand, stand, aanwezigen: [{gebruiker, naam, modus, app, apparaat, sinds}]}`,
	één regel per sessie (dus twee voor wie het op twee apparaten open heeft)."""
	_bestand(bestand, "read")
	return _werk_bij(bestand, "ruim")[1]


def ruim_verlopen_op() -> None:
	"""Scheduler, elke minuut: haal verlopen sessies weg en push de nieuwe lijst, ook als niemand er meer om vraagt."""
	for bestand in frappe.cache.smembers(BESTANDEN):
		_werk_bij(cstr(bestand), "ruim")
