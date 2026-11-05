# Samenvoegen van IFC: een bewijsschets

Stand: 5 november 2026. Over: `voeg_samen(basis, ons, hun)` in de crate `openaec-samenvoegen`
(openaec-cloud-desktop, PR #6 en #7, head `0655eda`). De property-based tests die deze schets toetsen, staan in een
eigen draft-PR op de branch van #7 (zie het rapport `../formeel-model.md`).

Dit is een schets, geen machinaal nagekeken bewijs. Hij zegt welke eigenschappen gelden voor de regels waarop de
bibliotheek is gebouwd, en onder welke voorwaarden die doorwerken naar echte IFC-bestanden. Hij bewijst niets over
de hele bibliotheek: de regels hieronder zijn een model van de code, en de voorwaarden in §5 zijn niet bewezen.

## 1. Het model: registers met een eigen regel

Na het lezen valt een versie uiteen in **registers**. Een register is één plek met één waarde. Er zijn drie soorten,
elk met een eigen regel (`samenvoegen.rs` op `0655eda`):

- **Gewone registers**, regel `m`: per object (sleutel: GlobalId) of het bestaat en het type; per object en per
  gewoon attribuut de waarde (met verwijzingen als GlobalId of als inhoudshash of anker van een knoop); per groep
  benoemde eigenschappen in een SET (op naam) de lijst van waarden; de groepen losse knopen.
- **Aantallen**, regel `n`: per element van een SET of BAG van verwijzingen met dezelfde identiteit, het aantal
  (functie `verzameling`).
- **Vluchtige velden**, regel `v`: velden die bij elk opslaan veranderen, zoals de tijdstempel in een
  `IfcOwnerHistory` en de HEADER-velden (`vluchtige_kant`, `header_vluchtig`).

Noteer een versie als een functie `x : R → W`, van registers naar waarden. "Bestaat niet" is ook een waarde (`⊥`).

## 2. De drie regels

Voor één register met waarden `b` (basis), `o` (ons) en `h` (hun):

```
m(b, o, h) = o          als h = b of h = o
           = h          als o = b (en h ≠ b)
           = conflict   anders

n(b, o, h) = o                      als h = b of h = o
           = h                      als o = b
           = max(0, o + h − b)      anders (bij een SET hoogstens 1)

v(b, o, h) = h          als o = b en h ≠ o
           = o          anders (nooit een conflict)
```

`m` staat in `besluit_object` en `beide_gewijzigd`; voor een benoemde groep vergelijkt `verzameling` de gesorteerde
lijsten van waarden met dezelfde drie regels. `n` staat in `verzameling` (r. 928-938): de code vergelijkt eerst de
lijsten per groep (gelijk aantal betekent bij één identiteit een gelijke lijst) en telt pas daarna, met een aftrekking
die niet onder 0 gaat. `v` staat in `vluchtige_kant` (r. 306).

Het resultaat van de hele samenvoeging is per register de uitkomst van zijn regel, of een conflict als één register
een conflict geeft. Daarna komen controles (koppelregel, vervangen relaties, verwijzingen, boom, plaatsingen,
zelfcontrole van het geschreven resultaat). Die controles krijgen het plan alleen te lezen (`&Plan`) en voegen alleen
conflicten toe (bijv. `controleer_resultaat`, r. 1229); ze veranderen nooit een waarde van het resultaat.

## 3. Eigenschappen per regel

Elke eigenschap gaat over één register en een uitkomst zonder conflict.

**E1. Identiteit.** `f(b, b, h) = h` en `f(b, o, b) = o`, voor `f` = `m`, `n` en `v`.
*Bewijs.* `o = b`: is `h = b`, dan geeft de eerste regel `o = b = h`; anders geeft `m` en `n` de tweede regel `h`,
en `v` de eerste regel `h`. `h = b`: `m` en `n` geven met de eerste regel `o`. De eerste regel van `v` vraagt `o = b` en
`h ≠ o`; met `h = b` sluit dat elkaar uit, dus `v` geeft `o`. ∎

**E2. Idempotent.** `f(b, x, x) = x`, voor `m`, `n` en `v`. *Bewijs.* `m` en `n`: de eerste regel. `v`: de eerste
regel vraagt `h ≠ o`; dus `o = x`. ∎

**E3. Symmetrie.** Voor `m` en `n`: `f(b, o, h)` is een conflict precies als `f(b, h, o)` een conflict is, en anders
zijn ze gelijk.
*Bewijs.* Een conflict (alleen bij `m`) betekent: `o ≠ b`, `h ≠ b` en `o ≠ h`. Dat is symmetrisch. Anders: is
`h ∈ {b, o}` of `o = b`, dan geven beide volgordes dezelfde waarde (`o`, `o` of `h`). Bij `n` is de laatste regel
`o + h − b` ook symmetrisch. ∎
**Geldt niet voor `v`:** wijzigen beide kanten een vluchtig veld anders, dan wint ons: `v(1, 2, 3) = 2` en
`v(1, 3, 2) = 3`. Dat is zo bedoeld (een tijdstempel is geen werk), maar het maakt de samenvoeging als geheel niet
symmetrisch in ons en hun.

**E4. Behoud (geen stil verlies).**
- `m`: is er geen conflict en wijzigde ons het register (`o ≠ b`), dan is `r = o`; net zo voor hun.
  *Bewijs.* `o ≠ b` sluit de tweede regel uit; zonder conflict geldt de eerste: `r = o`. Voor hun: `h ≠ b` sluit
  "`h = b`" uit; dan is `h = o` (`r = o = h`) of `o = b` (`r = h`). ∎
- `n`: zonder afkapping op 0 en buiten de snelle weg `o = h ≠ b` is `r − b = (o − b) + (h − b)`: beide wijzigingen
  tellen mee. Met afkapping (beide halen samen meer weg dan er was) gaat een deel van het weghalen verloren; bij
  `o = h ≠ b` telt de wijziging één keer (zie E8).
- `v`: geldt **niet**. Wijzigden beide kanten het veld, dan valt de wijziging van hun weg, zonder conflict. Zo
  bedoeld voor velden die bij elk opslaan veranderen.

Gevolg: zonder conflict bevat het resultaat elke wijziging van een gewoon register van ons en van hun. Dat is wat
`SyncClient.tla` aanneemt (samenvoegen zonder conflict = de vereniging van de bewerkingen). Die aanname geldt dus voor
gewone registers, niet voor vluchtige velden en niet precies voor aantallen. Het model is een beperkte abstractie,
geen bewezen contract voor elk IFC-register.

**E5. Stabiel bij opnieuw samenvoegen.** Is `r = f(b, o, h)` zonder conflict, dan:
- `f(h, r, h) = r`, voor `m`, `n` en `v` (E1);
- `f(b, r, h) = r`, voor `m` en `v`. *Bewijs voor `m`:* is `r = o` (want `h ∈ {b, o}`), dan staat er `m(b, o, h)`.
  Is `r = h` (want `o = b`), dan `m(b, h, h) = h` (E2). Voor `v` net zo: `r = o` geeft `v(b, o, h) = r`; `r = h`
  (want `o = b`) geeft `v(b, h, h) = h`. ∎
- **Niet voor `n`:** `n(1, 2, 3) = 4`, maar `n(1, 4, 3) = 6`.

**E6. De opslaan-lus.** De lus (`sla_op`) schrijft; is de basis verouderd, dan voegt hij samen met de vorige "hun"
als nieuwe basis: `r₁ = f(b, o, h₁)`, daarna `r₂ = f(h₁, r₁, h₂)`. Omdat de server alleen schrijft op de huidige
versie, is `h₂` gemaakt op `h₁` of een latere versie.

Voor `m` (en net zo voor `v`), per register, als stap 1 geen conflict gaf:

- ons wijzigde het register niet (`o = b`): `r₁ = h₁` en `r₂ = f(h₁, h₁, h₂) = h₂`, gelijk aan `f(b, b, h₂)`;
- ons wijzigde het wel, en `h₁ = b`: `r₁ = o` en `r₂ = f(b, o, h₂)`, gelijk aan samenvoegen in één keer;
- ons wijzigde het wel, en `h₁ = o` (een ander deed al hetzelfde): `r₁ = o` en `r₂ = f(o, o, h₂) = h₂`. In één keer
  zou `m(b, o, h₂)` een conflict geven als `h₂ ∉ {b, o}`. Maar `h₂` is geschreven op een versie die onze waarde al
  had: iemand veranderde die waarde daarna. Dat is geen stil verlies van onze wijziging.

Gaf stap 1 een conflict, dan schrijft de lus niets (een conflictkopie). Dat gebeurt ook waar samenvoegen in één keer
zou lukken: een derde zette het register van `b` naar `h₁` en daarna weer terug naar `b`. Dan is de lus strenger dan
nodig.

Samengevat voor `m`: elke uitkomst van de lus is gelijk aan samenvoegen in één keer, of strenger (een conflict), of
neemt een latere waarde over die geschreven is op een versie met onze waarde erin. ∎

**Voor `n` geldt dit niet.** Tegenvoorbeeld (zonder afkapping): `b = 1, o = 2, h₁ = 2, h₂ = 3`. De lus geeft
`n(1, 2, 2) = 2` en dan `n(2, 2, 3) = 3`; in één keer `n(1, 2, 3) = 4`. Oorzaak: de snelle weg `o = h₁` telt de
wijziging één keer, de formule twee keer. Alleen als geen stap de snelle weg met `o = h ≠ b` neemt en er niets wordt
afgekapt, geeft de lus hetzelfde als in één keer (`r₁ + h₂ − h₁ = o + h₂ − b`). Er gaat ook in het tegenvoorbeeld
geen element verloren: het verschil is één kopie van een element in een BAG.

**E7. Volgorde.** Drie clients A, B en C werken vanaf dezelfde basis en slaan na elkaar op via de lus.

- Raken hun wijzigingen verschillende gewone registers (of hetzelfde register met dezelfde waarde), dan is de
  eindstand **van de regels** dezelfde in elke volgorde: per register de enige gewijzigde waarde (E1, E2, E6).
  Voorwaarde: de controles achteraf keuren de tussenstanden en de eindstand goed. Wijzigingen in verschillende
  registers kunnen samen een fout maken die elk apart niet maakt (bijv. een kring in de boom). Dan geeft de controle
  een conflict aan wie als laatste schrijft, en hangt de uitkomst wel van de volgorde af.
- Wijzigen twee clients hetzelfde gewone register anders, dan hangt de uitkomst van de volgorde af: wie eerst
  schrijft, staat in de versie; de ander krijgt een conflict, en dus een conflictkopie. Geen van beide wijzigingen
  gaat stil verloren (E4, en de conflictkopie).
- Voor vluchtige velden hangt de uitkomst altijd van de volgorde af (de laatste schrijver wint); voor aantallen kan
  ze ervan afhangen (E6).

**E8. De snelle weg bij aantallen.** De eerste twee regels van `n` geven dezelfde uitkomst als de formule
`o + h − b`, behalve bij `o = h ≠ b`: daar geeft de snelle weg `o` (beide deden "hetzelfde"), en de formule
`2o − b`. De bibliotheek kiest `o`: twee kanten die allebei één kopie weghalen, halen dezelfde kopie weg. Die keuze
maakt E5 en E6 voor aantallen ongeldig (zie daar).

## 4. De controles achteraf

De controles (koppelregel, boom, plaatsingen, zelfcontrole) maken de samenvoeging strenger. Ze breken E1, E2, E4 en
E5 niet, want die gaan over een uitkomst zonder conflict, en een controle voegt alleen conflicten toe. Ze kunnen E3
(symmetrie van het conflict) breken als een controle niet symmetrisch is in ons en hun. De koppelregel is in het
ontwerp symmetrisch geformuleerd ("bij de ene kant … bij de andere kant"). Voor E7 zijn ze een voorwaarde (zie daar).

## 5. Wanneer gelden deze eigenschappen voor echte bestanden?

Wat de regels voor echte IFC-bestanden waard zijn, hangt af van drie voorwaarden. Daar zitten de risico's, en daar
richten de property-based tests zich op. Deze voorwaarden zijn niet bewezen.

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
elk opslaan opnieuw. Dan moet `verschil(x, hernummerd(x))` leeg zijn en moet de samenvoeging van een hernummerde
versie hetzelfde resultaat geven.

## 6. Wat de property-based tests vonden

De tests staan in draft-PR [openaec-cloud-desktop#17](https://github.com/OpenAEC-Foundation/openaec-cloud-desktop/pull/17)
(`crates/openaec-samenvoegen/tests/eigenschappen.rs`, op de branch van #7). Een generator maakt uit vier echte
bestanden (IFC2X3, IFC4, IFC4X3, ook een planning van Open Planner Studio) willekeurige bewerkingen: 18 soorten,
0 tot 4 per kant. Het orakel past de stappen van ons en hun na elkaar toe; is de volgorde om het even, dan moet de
samenvoeging precies dat geven. 128 gevallen per eigenschap.

| Eigenschap hier | Test | Uitkomst |
|---|---|---|
| E1 identiteit | `identiteit` | houdt |
| E2 idempotent | `idempotent` | houdt |
| E3 symmetrie (zonder vluchtige velden in het verschil) | `symmetrie` | houdt |
| E4 behoud, gelijk aan het orakel | `behoud_zonder_stil_verlies` | houdt |
| E4 voor vluchtige velden (de regel `v`) | `vluchtige_velden_volgens_de_regel` | **houdt niet** (bevinding 2) |
| E5 stabiel: (h, r, h), (b, r, h), (b, r, o) | `stabiel_in_de_opslaan_lus` | houdt |
| opnieuw opslaan na gelukt: (b, o, r), (b, h, r) | `opnieuw_opslaan_na_gelukt` | **houdt niet** (bevinding 1); zonder vluchtige velden wel |
| E7 volgorde: drie bewerkingen via `sla_op`, zes volgordes | `volgorde_maakt_niet_uit` | houdt |
| V3 nummering | `nummering_doet_niet_mee` | houdt |
| V2 schrijven en lezen | `schrijven_en_lezen` | houdt |
| geen paniek bij verminkte invoer | `geen_paniek` | houdt |
| het rapport klopt met de uitkomst | `rapport_klopt` | houdt |

`≡` betekent: het verschil volgens de bibliotheek is leeg (vluchtige velden tellen niet mee).

**Bevinding 1 (een conflict te veel).** Ons zet alleen de tijdstempel in de gedeelde `IfcOwnerHistory`; hun hangt een
nieuwe eigenschappenset aan een wand. Samenvoegen lukt: `r`. Slaat hun daarna dezelfde versie nog eens op met basis
`b` (bijvoorbeeld na een verloren antwoord), dan geeft `(b, hun, r)` een conflict `GedeeldAnker`, waar `r` verwacht
wordt. Er gaat niets verloren; het ontwerp zegt wel dat een vluchtig veld nooit een conflict geeft (§5.6).

**Bevinding 2 (een vluchtige waarde valt stil weg).** Ons hangt een nieuwe set aan een wand; hun geeft een ander object
een eigen `IfcOwnerHistory` en zet alle tijdstempels op een nieuwe waarde. Er komt geen conflict, maar de nieuwe set
houdt de oude tijdstempel. De zelfcontrole ziet het niet. Dit raakt alleen een vluchtig veld, geen werk.

**Wat de tests niet raken.** De generator maakt geen dubbele elementen in een BAG; het tegenvoorbeeld bij E5 en E6
voor aantallen (§3) wordt dus niet geraakt. Ook niet: een nieuwe taak of een andere volgorde in een planning (LIST,
bewust een conflict), een ander type, materiaal, geometrie of kleur, `IfcElementQuantity`, complexe instanties. Van
256 gemeten gevallen gaven er maar 5 een volgorde die niet om het even was; dat deel van het orakel krijgt weinig
gevallen.

**Toetsen de tests iets?** Twaalf bekende fouten, één voor één teruggezet in de bibliotheek, maakten elk de eigenschap
rood die hem moest vinden (`scripts/mutatieproef.py`).
