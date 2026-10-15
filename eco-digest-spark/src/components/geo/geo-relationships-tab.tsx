import { useEffect, useMemo, useRef, useState } from "react";
import ForceGraph2D from "react-force-graph-2d";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { GeoEntity, GeoRelationship, useGeoIssues, useRelationshipGraph } from "@/lib/geoKnowledgeApi";
import { useLanguage } from "@/lib/i18n";

// entity_type(24種)はcategorical色として使うには種類過多のため、少数グループへ畳み込む
// （geo_entity_service.ENTITY_TYPE_DISPLAY_CATEGORYと同じ分類、Python側とTS側で重複管理）。
const ENTITY_TYPE_DISPLAY_CATEGORY: Record<string, string> = {
  country: "state", territory: "state", government: "state", ministry: "state",
  political_party: "political_social", faction: "political_social", person: "political_social",
  religious_group: "political_social", ethnic_group: "political_social",
  civil_society: "political_social", media: "political_social", labor_union: "political_social",
  international_organization: "political_social",
  military: "security", security_service: "security", armed_group: "security",
  military_base: "security",
  state_owned_enterprise: "economic", company: "economic", industry_group: "economic",
  geographic_location: "geography_asset", port: "geography_asset", strait: "geography_asset",
  canal: "geography_asset", strategic_asset: "geography_asset",
};

const CATEGORY_LABELS: Record<string, string> = {
  state: "国家・政府", political_social: "政治・社会主体", security: "軍事・治安",
  economic: "経済主体", geography_asset: "地理・インフラ資産",
};

const CATEGORY_COLORS_LIGHT: Record<string, string> = {
  state: "#1d4ed8", political_social: "#7c3aed", security: "#b91c1c",
  economic: "#047857", geography_asset: "#92400e",
};
const CATEGORY_COLORS_DARK: Record<string, string> = {
  state: "#60a5fa", political_social: "#c4b5fd", security: "#fca5a5",
  economic: "#6ee7b7", geography_asset: "#fcd34d",
};

const RELATIONSHIP_DOMAINS = [
  "diplomacy", "security", "military", "trade", "investment", "finance", "energy",
  "technology", "information", "political_support", "social", "legal", "logistics",
];

function useIsDarkMode() {
  const [isDark, setIsDark] = useState(false);
  useEffect(() => {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    setIsDark(mq.matches);
    const listener = (e: MediaQueryListEvent) => setIsDark(e.matches);
    mq.addEventListener("change", listener);
    return () => mq.removeEventListener("change", listener);
  }, []);
  return isDark;
}

function entityLabel(entity: GeoEntity): string {
  return entity.name_ja || entity.canonical_name || entity.entity_id;
}

export function GeoRelationshipsTab({ themeId }: { themeId: string }) {
  const { t } = useLanguage();
  const issuesQuery = useGeoIssues(themeId);
  const issues = issuesQuery.data?.issues ?? [];
  const [issueId, setIssueId] = useState("");
  const [viewMode, setViewMode] = useState<"graph" | "table">("graph");
  const [enabledDomains, setEnabledDomains] = useState<Set<string>>(new Set(RELATIONSHIP_DOMAINS));
  const isDark = useIsDarkMode();
  const containerRef = useRef<HTMLDivElement>(null);
  const [graphWidth, setGraphWidth] = useState(600);

  useEffect(() => {
    if (!issueId && issues.length > 0) setIssueId(issues[0].issue_id);
  }, [issues, issueId]);

  useEffect(() => {
    if (!containerRef.current) return;
    const observer = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect.width;
      if (width) setGraphWidth(width);
    });
    observer.observe(containerRef.current);
    return () => observer.disconnect();
  }, []);

  const graphQuery = useRelationshipGraph(issueId);
  const nodes = graphQuery.data?.nodes ?? [];
  const edges = graphQuery.data?.edges ?? [];

  const filteredEdges = useMemo(
    () => edges.filter((e) => enabledDomains.has(e.relationship_domain)),
    [edges, enabledDomains],
  );
  const usedEntityIds = useMemo(
    () => new Set(filteredEdges.flatMap((e) => [e.from_entity_id, e.to_entity_id])),
    [filteredEdges],
  );
  const filteredNodes = useMemo(
    () => nodes.filter((n) => usedEntityIds.has(n.entity_id)),
    [nodes, usedEntityIds],
  );

  const colors = isDark ? CATEGORY_COLORS_DARK : CATEGORY_COLORS_LIGHT;

  const graphData = useMemo(() => ({
    nodes: filteredNodes.map((n) => ({
      id: n.entity_id, label: entityLabel(n), entityType: n.entity_type,
      category: ENTITY_TYPE_DISPLAY_CATEGORY[n.entity_type] ?? "political_social",
    })),
    links: filteredEdges.map((e) => ({
      source: e.from_entity_id, target: e.to_entity_id,
      domain: e.relationship_domain, type: e.relationship_type,
    })),
  }), [filteredNodes, filteredEdges]);

  const entityById = useMemo(() => {
    const map = new Map<string, GeoEntity>();
    nodes.forEach((n) => map.set(n.entity_id, n));
    return map;
  }, [nodes]);

  const toggleDomain = (domain: string) => {
    setEnabledDomains((prev) => {
      const next = new Set(prev);
      if (next.has(domain)) next.delete(domain); else next.add(domain);
      return next;
    });
  };

  return (
    <div className="space-y-4">
      <p className="text-xs text-muted-foreground">
        {t("geo.tabs.relationshipsExplain") || "Issue単位でEntity間のRelationshipをグラフ表示します。"}
      </p>

      <div className="flex flex-wrap items-center gap-3">
        <select className="border border-input rounded px-2 py-1 text-sm bg-background max-w-xs"
                value={issueId} onChange={(e) => setIssueId(e.target.value)}>
          {issues.length === 0 && <option value="">（Issueが未登録です）</option>}
          {issues.map((issue) => (
            <option key={issue.issue_id} value={issue.issue_id}>{issue.name_ja}</option>
          ))}
        </select>
        <div className="flex items-center gap-1">
          <Button size="sm" variant={viewMode === "graph" ? "default" : "outline"} onClick={() => setViewMode("graph")}>
            グラフ表示
          </Button>
          <Button size="sm" variant={viewMode === "table" ? "default" : "outline"} onClick={() => setViewMode("table")}>
            テーブル表示
          </Button>
        </div>
      </div>

      <div className="flex flex-wrap gap-x-4 gap-y-2">
        {RELATIONSHIP_DOMAINS.map((domain) => (
          <div key={domain} className="flex items-center gap-1.5">
            <Checkbox id={`domain-${domain}`} checked={enabledDomains.has(domain)}
                      onCheckedChange={() => toggleDomain(domain)} />
            <Label htmlFor={`domain-${domain}`} className="font-normal text-xs">{domain}</Label>
          </div>
        ))}
      </div>

      <div className="flex flex-wrap gap-3">
        {Object.entries(CATEGORY_LABELS).map(([key, label]) => (
          <span key={key} className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <span className="inline-block w-2.5 h-2.5 rounded-full" style={{ backgroundColor: colors[key] }} />
            {label}
          </span>
        ))}
      </div>

      {!issueId ? (
        <p className="text-sm text-muted-foreground">Issueを選択してください。</p>
      ) : graphQuery.isLoading ? (
        <p className="text-sm text-muted-foreground">...</p>
      ) : filteredNodes.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          このIssueに紐づくRelationshipがありません（フィルタ条件を確認してください）。
        </p>
      ) : viewMode === "graph" ? (
        <div ref={containerRef} className="card-paper rounded-lg overflow-hidden" style={{ height: 480 }}>
          <ForceGraph2D
            graphData={graphData}
            width={graphWidth}
            height={480}
            backgroundColor="transparent"
            nodeLabel={(node) => `${(node as { label: string }).label} (${(node as { entityType: string }).entityType})`}
            nodeColor={(node) => colors[(node as { category: string }).category] ?? colors.political_social}
            nodeRelSize={5}
            linkLabel={(link) => `${(link as { type: string }).type}（${(link as { domain: string }).domain}）`}
            linkColor={() => (isDark ? "rgba(226,232,240,0.35)" : "rgba(15,23,42,0.25)")}
            linkDirectionalArrowLength={4}
            linkDirectionalArrowRelPos={1}
            cooldownTicks={100}
          />
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-muted-foreground border-b border-border">
                <th className="py-2 pr-3">From</th>
                <th className="py-2 pr-3">To</th>
                <th className="py-2 pr-3">Domain</th>
                <th className="py-2 pr-3">Type</th>
                <th className="py-2 pr-3">Confidence</th>
              </tr>
            </thead>
            <tbody>
              {filteredEdges.map((edge: GeoRelationship) => (
                <tr key={edge.relationship_id} className="border-b border-border/50">
                  <td className="py-2 pr-3">{entityById.get(edge.from_entity_id)?.canonical_name ?? edge.from_entity_id}</td>
                  <td className="py-2 pr-3">{entityById.get(edge.to_entity_id)?.canonical_name ?? edge.to_entity_id}</td>
                  <td className="py-2 pr-3"><Badge variant="outline">{edge.relationship_domain}</Badge></td>
                  <td className="py-2 pr-3">{edge.relationship_type}</td>
                  <td className="py-2 pr-3 text-muted-foreground">{edge.confidence}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
