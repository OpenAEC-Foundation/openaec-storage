-------------------------- MODULE Wijzigingenlijst --------------------------
(***************************************************************************)
(* De wijzigingenlijst (Opslagwijziging) en de cursor van wijzigingen().   *)
(*                                                                         *)
(* Bronnen:                                                                *)
(*  - main e87b870, openaec_storage/api.py, wijzigingen(): filter op       *)
(*    name > sinds; bij een lege pagina wordt de cursor het hoogste name   *)
(*    van de hele site.                                                    *)
(*  - PR #16 (samenwerken/commitnummer), commitnummer.py veeg() en         *)
(*    api.py wijzigingen(): filter op commitnummer > sinds; de veger       *)
(*    nummert vastgelegde regels met nummer 0 onder het tellerslot en      *)
(*    commit vóór de lezing; bij een lege pagina blijft de cursor sinds.   *)
(*  - Ontwerp §5.4 (PR #8).                                                *)
(*                                                                         *)
(* Varianten (constante Variant):                                          *)
(*  "main"             : de code op main.                                  *)
(*  "pr16"             : de code van PR #16.                               *)
(*  "pr16_zonder_commit": als pr16, maar zonder stap 6 (de commit van de   *)
(*                       veger). Een GET-verzoek draait dan terug. Dit is  *)
(*                       een controle van het model zelf: het ontwerp zegt *)
(*                       dat dit fout gaat, en TLC moet dat vinden.        *)
(*                                                                         *)
(* Aannames over MariaDB/InnoDB:                                           *)
(*  A1 Een commit maakt alle regels van een transactie in één keer         *)
(*     zichtbaar.                                                          *)
(*  A2 name komt uit een sequence zonder cache: oplopend in de volgorde    *)
(*     van invoegen.                                                       *)
(*  A3 Vegers wachten op elkaar via SELECT ... FOR UPDATE op de            *)
(*     tellerregel; de momentopname van de veger begint na het slot.       *)
(*     Daarom is één veging hier één atomaire stap.                        *)
(*  A4 Eén lezing (een SELECT, of twee in dezelfde momentopname) is één    *)
(*     atomaire stap.                                                      *)
(***************************************************************************)
EXTENDS Naturals, Sequences, FiniteSets, TLC

CONSTANTS
    Schrijvers,     \* transacties die regels invoegen
    Lezers,         \* clients met een cursor
    Ruimtes,
    MaxRijen,       \* hoogstens zoveel regels per transactie
    Limiet,         \* paginagrootte van wijzigingen()
    Variant,
    LezerRuimte,    \* [Lezers -> Ruimtes]
    LezerDeling     \* [Lezers -> BOOLEAN]: leest via een deling

ASSUME Variant \in {"main", "pr16", "pr16_zonder_commit"}
ASSUME Limiet \in Nat \ {0}

VARIABLES
    rij,        \* Seq van [ruimte, deling, w, status, cn]; de index is name
    tx,         \* [Schrijvers -> {"nieuw", "open", "vast", "terug"}]
    teller,     \* de tellerregel van PR #16 (tabSeries)
    cursor,     \* [Lezers -> Nat]
    gezien,     \* [Lezers -> SUBSET Nat]: names die de lezer ontving
    dubbel      \* [Lezers -> BOOLEAN]: de lezer ontving een regel twee keer

vars == <<rij, tx, teller, cursor, gezien, dubbel>>

Namen == 1..Len(rij)

PR16 == Variant \in {"pr16", "pr16_zonder_commit"}

Max(S) == IF S = {} THEN 0 ELSE CHOOSE x \in S : \A y \in S : y <= x

\* De k kleinste elementen van een eindige verzameling getallen.
Kleinste(S, k) == {x \in S : Cardinality({y \in S : y < x}) < k}

\* Hoeveel regels heeft transactie w al ingevoegd.
AantalVan(w) == Cardinality({n \in Namen : rij[n].w = w})

Vast(n) == rij[n].status = "vast"

\* Regels die lezer l mag krijgen (ruimte en, bij een deling, binnen de gedeelde map).
VoorLezer(l, n) ==
    /\ rij[n].ruimte = LezerRuimte[l]
    /\ (LezerDeling[l] => rij[n].deling)

Init ==
    /\ rij = << >>
    /\ tx = [w \in Schrijvers |-> "nieuw"]
    /\ teller = 0
    /\ cursor = [l \in Lezers |-> 0]
    /\ gezien = [l \in Lezers |-> {}]
    /\ dubbel = [l \in Lezers |-> FALSE]

(***************************************************************************)
(* Schrijvers                                                              *)
(***************************************************************************)

\* _log(): een regel in dezelfde transactie als de handeling. name komt uit de sequence.
Voeg(w) ==
    /\ tx[w] \in {"nieuw", "open"}
    /\ AantalVan(w) < MaxRijen
    /\ \E r \in Ruimtes, d \in BOOLEAN :
          rij' = Append(rij, [ruimte |-> r, deling |-> d, w |-> w, status |-> "open", cn |-> 0])
    /\ tx' = [tx EXCEPT ![w] = "open"]
    /\ UNCHANGED <<teller, cursor, gezien, dubbel>>

\* Commit langs elke route (gewone commit, DDL, rauwe SQL, achtergrondtaak): alle open regels worden zichtbaar.
Commit(w) ==
    /\ tx[w] = "open"
    /\ rij' = [n \in Namen |-> IF rij[n].w = w /\ rij[n].status = "open"
                                THEN [rij[n] EXCEPT !.status = "vast"] ELSE rij[n]]
    /\ tx' = [tx EXCEPT ![w] = "vast"]
    /\ UNCHANGED <<teller, cursor, gezien, dubbel>>

Rollback(w) ==
    /\ tx[w] = "open"
    /\ rij' = [n \in Namen |-> IF rij[n].w = w /\ rij[n].status = "open"
                                THEN [rij[n] EXCEPT !.status = "terug"] ELSE rij[n]]
    /\ tx' = [tx EXCEPT ![w] = "terug"]
    /\ UNCHANGED <<teller, cursor, gezien, dubbel>>

\* Terug naar een savepoint: één regel vervalt, de transactie loopt door.
Savepoint(w, n) ==
    /\ tx[w] = "open"
    /\ n \in Namen
    /\ rij[n].w = w
    /\ rij[n].status = "open"
    /\ rij' = [rij EXCEPT ![n].status = "terug"]
    /\ UNCHANGED <<tx, teller, cursor, gezien, dubbel>>

(***************************************************************************)
(* De veger (PR #16, stappen 3 tot en met 6)                               *)
(***************************************************************************)

Nullen == {n \in Namen : Vast(n) /\ rij[n].cn = 0}

\* Nummering in volgorde van name, vanaf de teller.
Nummer(Z, t, n) == t + Cardinality({m \in Z : m <= n})

Veeg ==
    /\ Variant = "pr16"
    /\ Nullen # {}
    /\ LET Z == Nullen IN
          /\ rij' = [n \in Namen |-> IF n \in Z THEN [rij[n] EXCEPT !.cn = Nummer(Z, teller, n)]
                                                ELSE rij[n]]
          /\ teller' = teller + Cardinality(Z)
    /\ UNCHANGED <<tx, cursor, gezien, dubbel>>

(***************************************************************************)
(* Lezers: wijzigingen(ruimte, sinds = cursor, limiet)                     *)
(***************************************************************************)

Lever(l, pagina, nieuweCursor) ==
    LET geleverd == IF LezerDeling[l] THEN {n \in pagina : rij[n].deling} ELSE pagina IN
    /\ cursor' = [cursor EXCEPT ![l] = nieuweCursor]
    /\ gezien' = [gezien EXCEPT ![l] = @ \cup geleverd]
    /\ dubbel' = [dubbel EXCEPT ![l] = @ \/ (geleverd \cap gezien[l] # {})]

LeesMain(l) ==
    /\ Variant = "main"
    /\ LET K == {n \in Namen : Vast(n) /\ rij[n].ruimte = LezerRuimte[l] /\ n > cursor[l]}
           pagina == Kleinste(K, Limiet)
           hoogste == Max({n \in Namen : Vast(n)})   \* select max(name) over de hele site
       IN Lever(l, pagina, IF pagina # {} THEN Max(pagina) ELSE Max({cursor[l], hoogste}))
    /\ UNCHANGED <<rij, tx, teller>>

LeesPR16(l) ==
    /\ Variant = "pr16"
    /\ LET K == {n \in Namen : rij[n].cn > cursor[l] /\ rij[n].ruimte = LezerRuimte[l]}
           cns == {rij[n].cn : n \in K}
           paginaCn == Kleinste(cns, Limiet)
           pagina == {n \in K : rij[n].cn \in paginaCn}
       IN Lever(l, pagina, IF pagina # {} THEN Max(paginaCn) ELSE cursor[l])
    /\ UNCHANGED <<rij, tx, teller>>

\* Zonder stap 6: de veger nummert in de transactie van het verzoek, de lezer ziet die nummers,
\* en daarna draait het GET-verzoek alles terug. Alleen cursor en gezien blijven over.
LeesZonderCommit(l) ==
    /\ Variant = "pr16_zonder_commit"
    /\ LET Z == Nullen
           cnH(n) == IF n \in Z THEN Nummer(Z, teller, n) ELSE rij[n].cn
           K == {n \in Namen : cnH(n) > cursor[l] /\ rij[n].ruimte = LezerRuimte[l]}
           cns == {cnH(n) : n \in K}
           paginaCn == Kleinste(cns, Limiet)
           pagina == {n \in K : cnH(n) \in paginaCn}
       IN Lever(l, pagina, IF pagina # {} THEN Max(paginaCn) ELSE cursor[l])
    /\ UNCHANGED <<rij, tx, teller>>

Lees(l) == LeesMain(l) \/ LeesPR16(l) \/ LeesZonderCommit(l)

Next ==
    \/ \E w \in Schrijvers : Voeg(w) \/ Commit(w) \/ Rollback(w)
    \/ \E w \in Schrijvers, n \in Namen : Savepoint(w, n)
    \/ Veeg
    \/ \E l \in Lezers : Lees(l)

Fairness ==
    /\ WF_vars(Veeg)
    /\ \A l \in Lezers : WF_vars(Lees(l))
    /\ \A w \in Schrijvers : WF_vars(Commit(w) \/ Rollback(w))

Spec == Init /\ [][Next]_vars
FairSpec == Spec /\ Fairness

(***************************************************************************)
(* Eigenschappen                                                           *)
(***************************************************************************)

TypeOK ==
    /\ \A n \in Namen : rij[n].status \in {"open", "vast", "terug"}
    /\ tx \in [Schrijvers -> {"nieuw", "open", "vast", "terug"}]
    /\ cursor \in [Lezers -> Nat]

\* De sleutel waarop de lezer filtert.
Sleutel(n) == IF PR16 THEN rij[n].cn ELSE n

\* E1. De lijst slaat nooit een vastgelegde regel over: een regel die de lezer mag krijgen en nog niet
\* kreeg, ligt nog vóór hem (sleutel boven de cursor), of heeft nog geen nummer (en krijgt er later een
\* boven de teller, zie E2).
GeenOverslaan ==
    \A l \in Lezers, n \in Namen :
        (Vast(n) /\ VoorLezer(l, n) /\ n \notin gezien[l])
            => (Sleutel(n) > cursor[l] \/ (PR16 /\ Sleutel(n) = 0))

\* E2. (PR #16) Geen cursor boven de teller: een regel die later een nummer krijgt, komt boven elke cursor.
CursorOnderTeller == PR16 => \A l \in Lezers : cursor[l] <= teller

\* E3. Geen regel twee keer.
GeenDubbel == \A l \in Lezers : ~dubbel[l]

\* E4. Een nummer wijst één regel aan.
NummersUniek ==
    PR16 => \A m, n \in Namen : (m # n /\ rij[m].cn # 0) => rij[m].cn # rij[n].cn

\* E5 (levendheid). Een lezer die blijft lezen, krijgt elke vastgelegde regel die voor hem is.
KrijgtAlles ==
    \A l \in Lezers : \A n \in 1..(Cardinality(Schrijvers) * MaxRijen) :
        (n \in Namen /\ Vast(n) /\ VoorLezer(l, n)) ~> (n \in gezien[l])

=============================================================================
