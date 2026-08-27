from __future__ import annotations

import unittest

import networkx as nx

from rpd.network_explorer import interactive_network_html


class NetworkExplorerTests(unittest.TestCase):
    def test_self_contained_graph_contains_clickable_node_and_edge_details(self):
        graph = nx.MultiDiGraph()
        graph.add_node(
            1, label="Target plc", description="Target entity summary.", target=True,
            risk=False, scope="LEGAL_ENTITY", lei="LEI-TARGET", country="GB",
        )
        graph.add_node(
            2, label="Partner Ltd", description="Partner entity summary.", target=False,
            risk=True, scope="LEGAL_ENTITY", country="AU",
        )
        graph.add_edge(
            1, 2, assertion_id=7, label="PARTNERED_WITH",
            definition="Has an explicit commercial partnership.",
            description="Target plc partnered with Partner Ltd.",
            classification="COUNTERPARTY", confidence="HIGH",
            validation_status="VALIDATED", explicit_or_inferred="EXPLICIT",
            event_date="2026-01-01", evidence="The parties entered a partnership.",
            source_url="https://example.test/source", source_title="Disclosure",
        )
        rendered = interactive_network_html(graph)
        self.assertIn('data-node-id="1"', rendered)
        self.assertIn('data-edge-id="7"', rendered)
        self.assertIn("Entity information", rendered)
        self.assertIn("Relationship information", rendered)
        self.assertIn("Click a node or edge for details", rendered)
        self.assertIn('id="node-label-toggle"', rendered)
        self.assertIn('id="edge-label-toggle"', rendered)
        self.assertIn('id="graph-search"', rendered)
        self.assertIn('id="search-kind"', rendered)
        self.assertIn('id="entity-type-filter"', rendered)
        self.assertIn('id="relation-type-filter"', rendered)
        self.assertIn('id="confidence-filter"', rendered)
        self.assertIn('id="loop-toggle"', rendered)
        self.assertIn("confidence-high", rendered)
        self.assertIn("draggingNode", rendered)
        self.assertIn("selectNode(completedNode.dataset.nodeId)", rendered)
        self.assertIn("node.setPointerCapture", rendered)
        self.assertIn("hasRelationshipFilter", rendered)
        self.assertIn(".canvas.hide-node-labels .node.selected .node-label{display:block}", rendered)
        self.assertIn(".canvas.hide-edge-labels .edge.selected .edge-label{display:block}", rendered)
        self.assertNotIn("https://cdn", rendered)

    def test_other_material_relation_exposes_specific_type_and_loop_hint(self):
        graph = nx.MultiDiGraph()
        graph.add_node(1, label="Alpha", target=True)
        graph.add_node(2, label="Beta")
        graph.add_edge(
            1, 2, assertion_id=11, label="OTHER_MATERIAL_RELATION",
            proposed_relation_type="OFFTAKE_AGREEMENT_WITH",
            assertion_text="Alpha entered an offtake agreement with Beta.",
            confidence="LOW",
        )
        graph.add_edge(
            2, 1, assertion_id=12, label="SUPPLIES", confidence="UNVERIFIED",
        )
        rendered = interactive_network_html(graph)
        self.assertIn("OFFTAKE_AGREEMENT_WITH", rendered)
        self.assertIn("Evidence-supported relationship statement", rendered)
        self.assertIn('data-edge-id="11"', rendered)
        self.assertIn("loop_hint\":true", rendered)
        self.assertIn("confidence-low loop-hint", rendered)

    def test_embedded_data_cannot_break_out_of_script_or_markup(self):
        malicious = "</script><script>alert('x')</script>"
        graph = nx.MultiDiGraph()
        graph.add_node(1, label=malicious, description=malicious, target=True)
        rendered = interactive_network_html(graph)
        self.assertNotIn(malicious, rendered)
        self.assertIn("\\u003c/script\\u003e", rendered)


if __name__ == "__main__":
    unittest.main()
