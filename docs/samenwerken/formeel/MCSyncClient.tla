------------------------------ MODULE MCSyncClient ------------------------------
\* Modelwaarden voor TLC.
EXTENDS SyncClient
MCClients2 == {"anna", "piet"}
MCClients3 == {"anna", "piet", "kees"}
MCPaden1 == {"f1"}
MCPaden2 == {"f1", "f2"}
MCIfc == {"f1"}
MCBestanden == {"B1", "B2", "B3", "B4", "B5"}
Symmetrie2 == Permutations(MCClients2)
Symmetrie3 == Permutations(MCClients3)
=============================================================================
