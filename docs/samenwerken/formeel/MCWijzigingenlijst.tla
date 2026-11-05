------------------------ MODULE MCWijzigingenlijst ------------------------
\* Modelwaarden voor TLC: 2 ruimtes, 2 lezers (een gewone en een via een deling).
\* Twee groottes: 3 transacties met elk 1 regel, of 2 transacties met elk 2 regels (met savepoint).
EXTENDS Wijzigingenlijst
MCSchrijvers3 == {"w1", "w2", "w3"}
MCSchrijvers2 == {"w1", "w2"}
MCLezers == {"l1", "l2"}
MCRuimtes == {"r1", "r2"}
MCLezerRuimte == [l \in MCLezers |-> "r1"]
MCLezerDeling == [l \in MCLezers |-> l = "l2"]
=============================================================================
