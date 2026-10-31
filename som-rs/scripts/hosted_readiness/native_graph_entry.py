"""Run the fixed Graph controller after the native bootstrap ledger is active."""
from __future__ import annotations


def main():
    from _som_native_active_controls_binding import controls
    controls.verify_installed()
    if controls.role_sources.get('graph.controller') != 'som-rs/scripts/hosted_readiness/same_candidate_graph.py':
        raise ValueError('fixed active Graph controller source differs')
    graph = controls.load_module('graph.controller')
    return graph.main()


if __name__ == '__main__':
    raise SystemExit(main())
