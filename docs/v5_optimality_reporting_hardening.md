# V5 cost-aware optimality reporting

`best_validated_incumbent` records the selected validated schedule and the
solver status and fingerprint of the domain where it was found.
`final_candidate_domain` separately records the last domain, its solver
status, proof, objective bound and gap. Bounds are emitted only when the final
domain's solver result was independently validated. An unsolved or failed
expanded domain has no reported bound or gap. The top-level optimization
status is `FEASIBLE` when the retained incumbent is valid but the final domain
is not proven optimal. This does not imply global continuous-time optimality.

Run: `python -m unittest tests.test_v5_optimality_reporting`.
