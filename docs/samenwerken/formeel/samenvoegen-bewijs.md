# Samenvoegen van IFC: een bewijsschets

Stand: 5 november 2026. Over: `voeg_samen(basis, ons, hun)` in de crate `openaec-samenvoegen`
(openaec-cloud-desktop, PR #6 en #7, head `0655eda`). De property-based tests die deze schets toetsen, staan in een
eigen draft-PR op de branch van #7 (zie het rapport `../formeel-model.md`).

Dit is een schets, geen machinaal nagekeken bewijs. Hij zegt welke eigenschappen gelden voor de regel waarop de
bibliotheek is gebouwd, en onder welke voorwaarden die doorwerken naar echte IFC-bestanden.

## 1. Het model: registers

Na het lezen valt een versie uiteen in **registers**. Een register is één plek met één waarde:

- per object (sleutel: GlobalId) de vraag of het bestaat, en het type;
- per object en per attribuut de waarde (met verwijzingen naar andere objecten als GlobalId, en naar knopen als
  inhoudshash of anker);
- per SET of BAG van verwijzingen: per element (per identiteit, of per naam bij een eigenschap) het aantal;
- de losse knopen en de HEADER-velden.

Noteer een versie als een functie `x : R → W`, van registers naar waarden. "Bestaat niet" is ook een waarde (`⊥`).

## 2. De regel per register

Voor één register met waarden `b` (basis), `o` (ons) en `h` (hun) gebruikt de bibliotheek
(`besluit_object` en `beide_gewijzigd` in `samenvoegen.rs`):

```
m(b, o, h) = o          als h = b of h = o
           = h          als o = b (en h ≠ b)
           = conflict   anders
```

Voor een element van een SET of BAG met aantallen `b, o, h` (functie `verzameling`):

```
n(b, o, h) = o                      als h = b of h = o
           = h                      als o = b
           = max(0, o + h − b)      anders (bij een SET hoogstens 1)
           = conflict               anders, bij een eigenschap op naam
```

Het resultaat van de hele samenvoeging is `r(x) = m(b(x), o(x), h(x))` voor elk register `x`, of een conflict als één
register een conflict geeft. Daarna komen controles (koppelregel, vervangen relaties, verwijzingen, boom,
plaatsingen, zelfcontrole van het geschreven resultaat). Die controles kunnen alleen een conflict **toevoegen**; ze
veranderen nooit een waarde van het resultaat.

## 3. Eigenschappen van de regel `m`

Elke eigenschap geldt per register, en dus voor de hele versie, zolang er geen conflict is.

**E1. Identiteit.** `m(b, b, h) = h` en `m(b, o, b) = o`.
*Bewijs.* `o = b`: is `h = b`, dan geeft de eerste regel `o = b = h`; anders de tweede regel `h`. `h = b`: de eerste
regel geeft `o`. ∎

**E2. Idempotent.** `m(b, x, x) = x`. *Bewijs.* De eerste regel, met `h = o = x`. ∎

**E3. Symmetrie.** `m(b, o, h)` is een conflict precies als `m(b, h, o)` een conflict is, en anders zijn ze gelijk.
*Bewijs.* Een conflict betekent: `o ≠ b`, `h ≠ b` en `o ≠ h`. Dat is symmetrisch in `o` en `h`. Geen conflict: dan is
`h ∈ {b, o}` of `o = b`. In elk van die drie gevallen geven beide volgordes dezelfde waarde (`o`, `o`, of `h`). ∎

**E4. Behoud (geen stil verlies).** Is er geen conflict en wijzigde ons het register (`o ≠ b`), dan is `r = o`. Net zo
voor hun.
*Bewijs.* `o ≠ b` sluit de tweede regel uit. Zonder conflict geldt dan de eerste regel: `r = o`. Voor hun: `h ≠ b`
sluit "`h = b`" uit; dan is `h = o` (eerste regel, `r = o = h`) of `o = b` (tweede regel, `r = h`). ∎

Gevolg: zonder conflict bevat het resultaat elke wijziging van ons en elke wijziging van hun, met dezelfde waarde.
Dit is het contract dat `SyncClient.tla` aanneemt: daar is samenvoegen zonder conflict de vereniging van de
bewerkingen.

**E5. Stabiel bij opnieuw samenvoegen.** Is `r = m(b, o, h)` zonder conflict, dan `m(h, r, h) = r` en
`m(b, r, h) = r`.
*Bewijs.* De eerste volgt uit E1. Voor de tweede: is `r = o` (want `h ∈ {b, o}`), dan staat er `m(b, o, h) = r`. Is
`r = h` (want `o = b`), dan staat er `m(b, h, h) = h` (E2). ∎

**E6. De opslaan-lus verliest niets stil.** De lus (`sla_op`) schrijft; is de basis verouderd, dan voegt hij samen
met de vorige "hun" als nieuwe basis: `r₁ = m(b, o, h₁)`, daarna `r₂ = m(h₁, r₁, h₂)`. Omdat de server alleen schrijft
op de huidige versie (vergelijk-en-schrijf), is `h₂` gemaakt op `h₁` of een latere versie. Per register, als stap 1
geen conflict gaf:

- ons wijzigde het register niet (`o = b`): `r₁ = h₁` en `r₂ = m(h₁, h₁, h₂) = h₂`, gelijk aan `m(b, b, h₂)`;
- ons wijzigde het wel, en `h₁ = b`: `r₁ = o` en `r₂ = m(b, o, h₂)`, gelijk aan samenvoegen in één keer;
- ons wijzigde het wel, en `h₁ = o` (een ander deed al hetzelfde): `r₁ = o` en `r₂ = m(o, o, h₂) = h₂`. In één keer
  zou `m(b, o, h₂)` een conflict geven als `h₂ ∉ {b, o}`. Maar `h₂` is geschreven op een versie die onze waarde al
  had: iemand veranderde die waarde daarna bewust. Dat is geen stil verlies.

Gaf stap 1 een conflict, dan schrijft de lus niets (een conflictkopie). Dat gebeurt ook in een geval waar
samenvoegen in één keer zou lukken: een derde zette het register van `b` naar `h₁` en daarna weer terug naar `b`. Dan
is de lus strenger dan nodig.

Samengevat: elke uitkomst van de lus is gelijk aan samenvoegen in één keer, of strenger (een conflict), of neemt een
latere waarde over die geschreven is op een versie met onze waarde erin. Voor de aantallen van een SET (zonder
afkapping op 0) is het zelfs gelijk: `r₁ + h₂ − h₁ = o + h₁ − b + h₂ − h₁ = o + h₂ − b`. ∎

**E7. Volgorde.** Drie clients A, B en C werken vanaf dezelfde basis en slaan na elkaar op via de lus.

- Raken hun wijzigingen verschillende registers (of hetzelfde register met dezelfde waarde), dan is de eindstand
  dezelfde in elke volgorde: per register de enige gewijzigde waarde. *Bewijs:* per register is er hoogstens één
  waarde `v ≠ b`. Met E1, E2 en E6 komt die waarde in elke volgorde in het resultaat, en geen ander register
  verandert. ∎
- Wijzigen twee clients hetzelfde register anders, dan hangt de uitkomst wel van de volgorde af: wie eerst schrijft,
  staat in de versie; de ander krijgt een conflict, en dus een conflictkopie. Geen van beide wijzigingen gaat stil
  verloren (E4, en de conflictkopie).

**E8. Aantallen in een SET of BAG.** Zonder afkapping op 0 is `n(b, o, h) = o + h − b`. Die vorm is symmetrisch (E3),
geeft `n(b, b, h) = h` (E1) en geeft in de lus hetzelfde als in één keer (E6). De eerste twee regels van `n` zijn
een snelle weg die dezelfde uitkomst geeft, behalve bij `o = h ≠ b` in een BAG: daar geeft de snelle weg `o` (beide
deden "hetzelfde"), en de formule `2o − b`. De bibliotheek kiest `o`: twee kanten die allebei één kopie weghalen,
halen dezelfde kopie weg. Met afkapping op 0 (beide halen meer weg dan er was) kan de lus een ander aantal geven dan
samenvoegen in één keer; dat geval komt alleen voor als beide kanten hetzelfde element weghalen.

## 4. Wanneer gelden deze eigenschappen voor echte bestanden?

De regels hierboven zijn eenvoudig en kloppen. Wat ze voor echte IFC-bestanden waard zijn, hangt af van drie
voorwaarden. Daar zitten de risico's, en daar richten de property-based tests zich op.

**V1. Hetzelfde ding krijgt in elke versie hetzelfde register.** Een object heeft zijn GlobalId. Een knoop zonder
GlobalId (geometrie, plaatsing) krijgt een identiteit uit zijn inhoud en zijn ankers (ontwerp §5.6, "Ankers", K14,
K17). Gaat dat mis, dan ziet de bibliotheek een wijziging als "weg en nieuw". Voorbeeld: twee gelijke contexten in
een SET, met een andere volgorde in de tekst. De bibliotheek koppelt zulke leden alleen als dat eenduidig kan; anders
telt ze het als weg aan de ene kant en nieuw aan de andere. Dat geeft hoogstens een conflict te veel. Een stil
verlies kan hier alleen ontstaan als twee verschillende dingen ten onrechte hetzelfde register krijgen.

**V2. Het geschreven resultaat is wat het plan zegt.** De schrijver begint bij de tekst van ons en vervangt alleen
gewijzigde objecten. Daarna leest de bibliotheek het resultaat opnieuw en vergelijkt het met het plan
(`controleer_resultaat`, de zelfcontrole). Wijkt het af, dan is het een conflict. Een fout in de schrijver kan dus
alleen ongemerkt blijven als de zelfcontrole hetzelfde blinde punt heeft.

**V3. Nummering doet niet mee.** STEP-nummers (`#123`) tellen niet; een programma als Open Planner Studio nummert bij
elk opslaan opnieuw. Dan moeten `verschil(x, hernummerd(x))` leeg zijn en de samenvoeging van een hernummerde versie
hetzelfde resultaat geven.

De controles achteraf (koppelregel, boom, plaatsingen) maken de regel strenger. Ze breken E1, E2, E4 en E5 niet, want
die gaan over een uitkomst zonder conflict, en een controle voegt alleen conflicten toe. Ze kunnen E3 (symmetrie van
het conflict) breken als een controle niet symmetrisch is in ons en hun. De koppelregel is in het ontwerp symmetrisch
geformuleerd ("bij de ene kant … bij de andere kant").

## 5. Wat de property-based tests toetsen

| Eigenschap | Test (`tests/eigenschappen.rs`) |
|---|---|
| E1 identiteit | `voeg_samen(b, b, h) ≡ h`, `voeg_samen(b, o, b) ≡ o` |
| E2 idempotent | `voeg_samen(b, x, x) ≡ x` |
| E3 symmetrie | conflict ⇔ conflict met ons en hun omgewisseld; anders ≡ |
| E4 behoud | elke wijziging uit `verschil(b, o)` en `verschil(b, h)` staat in `verschil(b, r)` |
| E5 stabiel | `voeg_samen(h, r, h) ≡ r` en `voeg_samen(b, r, h) ≡ r` |
| E7 volgorde | drie bewerkingen op verschillende objecten, in elke volgorde via de lus |
| V3 nummering | `verschil(x, hernummerd(x))` leeg; samenvoegen na hernummeren gelijk |
| V2 schrijven | het resultaat is weer te lezen en verandert niet bij nog eens lezen en schrijven |

`≡` betekent: het verschil volgens de bibliotheek is leeg. De uitkomsten staan in de draft-PR van de tests en in het
rapport.
