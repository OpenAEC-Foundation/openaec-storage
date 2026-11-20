------------------------------ MODULE MCSyncClient ------------------------------
\* Modelwaarden voor TLC.
EXTENDS SyncClient
MCClients2 == {"anna", "piet"}
MCClients3 == {"anna", "piet", "kees"}
MCPaden1 == {"f1"}
MCPaden2 == {"f1", "f2"}
MCIfc == {"f1"}
MCPadenNietIfc == {"f2"}
MCKopie1 == {"k1"}
MCKopie2 == {"k1", "k2"}
MCKopie3 == {"k1", "k2", "k3"}
MCBestanden == {"B1", "B2", "B3", "B4", "B5", "B6"}
Symmetrie2 == Permutations(MCClients2)
Symmetrie3 == Permutations(MCClients3)
=============================================================================
