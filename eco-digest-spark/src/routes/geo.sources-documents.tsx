import { createFileRoute } from "@tanstack/react-router";
import { useState } from "react";
import { TopBar } from "@/components/top-bar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useGeoDocuments, useGeoThemes } from "@/lib/api";
import {
  useChunkDocument, useGeoSources, useIngestionRuns, useRunImfConnector, useRunManifestoConnector,
  useRunOfacConnector, useRunUnComtradeConnector, useRunWorldBankConnector, useRunWtoConnector,
  useSetSourceReferenceAvailability,
} from "@/lib/geoKnowledgeApi";
import { useLanguage } from "@/lib/i18n";

export const Route = createFileRoute("/geo/sources-documents")({
  component: GeoSourcesDocumentsPage,
});

function GeoSourcesDocumentsPage() {
  const { t } = useLanguage();
  const { data: themesData } = useGeoThemes();
  const themes = themesData?.themes ?? [];
  const [themeId, setThemeId] = useState("");
  const documentsQuery = useGeoDocuments(themeId || themes[0]?.id || "");
  const chunkMutation = useChunkDocument();
  const [chunkCounts, setChunkCounts] = useState<Record<string, number>>({});

  const [entityId, setEntityId] = useState("");
  const [countryCode, setCountryCode] = useState("");
  const [maxRecords, setMaxRecords] = useState("");
  const [partnerEntityId, setPartnerEntityId] = useState("");
  const [manifestoMaxRecords, setManifestoMaxRecords] = useState("");
  const worldBankMutation = useRunWorldBankConnector();
  const ofacMutation = useRunOfacConnector();
  const imfMutation = useRunImfConnector();
  const unComtradeMutation = useRunUnComtradeConnector();
  const wtoMutation = useRunWtoConnector();
  const manifestoMutation = useRunManifestoConnector();
  const ingestionRunsQuery = useIngestionRuns();
  const sourcesQuery = useGeoSources();
  const sources = sourcesQuery.data?.sources ?? [];
  const credentialConfigured = (connectorType: string) =>
    sources.find((s) => s.connector_type === connectorType)?.credential_configured ?? true;
  const referenceAvailabilityMutation = useSetSourceReferenceAvailability();
  const [stewardId, setStewardId] = useState("steward1");

  return (
    <>
      <TopBar breadcrumb={[{ label: t("nav.geoSourcesDocuments") }]} />
      <main className="flex-1 max-w-[1000px] w-full mx-auto px-8 py-8 space-y-6">
        <section className="border-b border-border pb-5">
          <h1 className="text-editorial text-4xl">{t("geo.sourcesDocuments.title")}</h1>
          <p className="text-sm text-muted-foreground mt-2">{t("geo.sourcesDocuments.subtitle")}</p>
        </section>

        <select className="border border-input rounded px-2 py-1 text-sm bg-background max-w-xs"
                value={themeId} onChange={(e) => setThemeId(e.target.value)}>
          {themes.map((th) => <option key={th.id} value={th.id}>{th.nameJa}</option>)}
        </select>

        <div className="card-paper rounded-lg p-4 space-y-3">
          <h2 className="text-editorial text-lg">Connector（Phase2A: World Bank / OFAC）</h2>
          <div className="grid grid-cols-3 gap-2 items-end">
            <div className="space-y-1">
              <label className="text-xs text-muted-foreground">Entity ID</label>
              <Input value={entityId} onChange={(e) => setEntityId(e.target.value)} placeholder="geo_entities.entity_id" />
            </div>
            <div className="space-y-1">
              <label className="text-xs text-muted-foreground">Country Code</label>
              <Input value={countryCode} onChange={(e) => setCountryCode(e.target.value)} placeholder="JP" />
            </div>
            <Button variant="outline" disabled={worldBankMutation.isPending || !entityId || !countryCode}
                    onClick={() => worldBankMutation.mutate({
                      theme_id: themeId || themes[0]?.id || "", entity_id: entityId, country_code: countryCode,
                    })}>
              World Bank実行
            </Button>
          </div>
          <div className="grid grid-cols-3 gap-2 items-end">
            <div className="space-y-1">
              <label className="text-xs text-muted-foreground">Max Records（テスト用、空欄で全件）</label>
              <Input value={maxRecords} onChange={(e) => setMaxRecords(e.target.value)} placeholder="例: 50" />
            </div>
            <Button variant="outline" disabled={ofacMutation.isPending}
                    onClick={() => ofacMutation.mutate({
                      theme_id: themeId || themes[0]?.id || "",
                      max_records: maxRecords ? Number(maxRecords) : undefined,
                    })}>
              OFAC実行
            </Button>
          </div>
          {(worldBankMutation.isError || ofacMutation.isError) && (
            <p className="text-xs text-destructive">
              {((worldBankMutation.error ?? ofacMutation.error) as Error)?.message}
            </p>
          )}
        </div>

        <div className="card-paper rounded-lg p-4 space-y-3">
          <h2 className="text-editorial text-lg">Connector（Phase2B: IMF / UN Comtrade / WTO / Manifesto Project）</h2>

          <div className="grid grid-cols-2 gap-2 items-end">
            <p className="text-xs text-muted-foreground col-span-2">
              IMF（無認証、実アクセス確認済み）— 上記Entity ID / Country Codeを使用
            </p>
            <Button variant="outline" disabled={imfMutation.isPending || !entityId || !countryCode}
                    onClick={() => imfMutation.mutate({
                      theme_id: themeId || themes[0]?.id || "", entity_id: entityId, country_code: countryCode,
                    })}>
              IMF実行
            </Button>
          </div>

          <div className="grid grid-cols-2 gap-2 items-end pt-2 border-t border-border">
            <div className="space-y-1">
              <label className="text-xs text-muted-foreground">Partner Entity ID（任意、貿易相手国）</label>
              <Input value={partnerEntityId} onChange={(e) => setPartnerEntityId(e.target.value)}
                     placeholder="geo_entities.entity_id" />
            </div>
            <Button variant="outline"
                    disabled={unComtradeMutation.isPending || !entityId || !credentialConfigured("un_comtrade")}
                    onClick={() => unComtradeMutation.mutate({
                      theme_id: themeId || themes[0]?.id || "", reporter_entity_id: entityId,
                      partner_entity_id: partnerEntityId || undefined,
                    })}>
              UN Comtrade実行
            </Button>
            {!credentialConfigured("un_comtrade") && (
              <p className="text-xs text-muted-foreground col-span-2">
                APIキー未設定（config.jsonのconnector_credentials.un_comtrade.env_varが指す環境変数を設定してください）
              </p>
            )}
          </div>

          <div className="grid grid-cols-2 gap-2 items-end pt-2 border-t border-border">
            <Button variant="outline" disabled={wtoMutation.isPending || !entityId || !credentialConfigured("wto")}
                    onClick={() => wtoMutation.mutate({ theme_id: themeId || themes[0]?.id || "", entity_id: entityId })}>
              WTO実行（上記Entity ID使用）
            </Button>
            {!credentialConfigured("wto") && (
              <p className="text-xs text-muted-foreground">
                APIキー未設定（connector_credentials.wto.env_var）
              </p>
            )}
          </div>

          <div className="grid grid-cols-2 gap-2 items-end pt-2 border-t border-border">
            <div className="space-y-1">
              <label className="text-xs text-muted-foreground">Max Records（テスト用、空欄で全件）</label>
              <Input value={manifestoMaxRecords} onChange={(e) => setManifestoMaxRecords(e.target.value)}
                     placeholder="例: 20" />
            </div>
            <Button variant="outline"
                    disabled={manifestoMutation.isPending || !credentialConfigured("manifesto")}
                    onClick={() => manifestoMutation.mutate({
                      theme_id: themeId || themes[0]?.id || "",
                      max_records: manifestoMaxRecords ? Number(manifestoMaxRecords) : undefined,
                    })}>
              Manifesto Project実行（Main Datasetのみ）
            </Button>
            {!credentialConfigured("manifesto") && (
              <p className="text-xs text-muted-foreground col-span-2">
                APIキー未設定（connector_credentials.manifesto_project.env_var）
              </p>
            )}
          </div>

          {(imfMutation.isError || unComtradeMutation.isError || wtoMutation.isError || manifestoMutation.isError) && (
            <p className="text-xs text-destructive">
              {((imfMutation.error ?? unComtradeMutation.error ?? wtoMutation.error ?? manifestoMutation.error) as Error)?.message}
            </p>
          )}

          <div className="pt-2 border-t border-border space-y-1">
            <p className="text-xs text-muted-foreground">Ingestion Run一覧</p>
            {(ingestionRunsQuery.data?.ingestion_runs ?? []).map((run) => (
              <div key={run.ingestion_run_id} className="text-xs flex items-center gap-2">
                <Badge variant={run.status === "completed" ? "default" :
                                run.status === "failed" ? "destructive" : "secondary"}>{run.status}</Badge>
                <span>{run.connector_type}</span>
                <span className="tabular-nums text-muted-foreground">
                  fetched={run.fetched_count} inserted={run.inserted_count} updated={run.updated_count} error={run.error_count}
                </span>
              </div>
            ))}
          </div>
        </div>

        <div className="card-paper rounded-lg p-4 space-y-3">
          <h2 className="text-editorial text-lg">Source Registry — Reference Policy（Phase2B-2）</h2>
          <p className="text-xs text-muted-foreground">
            Sourceの信頼性・利用条件についての運用判断（内容の正誤判定ではない）。availableにすると、
            このSource由来のClaimがHuman Reviewなしで専門家AIから参照可能になる。
          </p>
          <div className="space-y-1">
            <label className="text-xs text-muted-foreground">Steward ID</label>
            <Input value={stewardId} onChange={(e) => setStewardId(e.target.value)} className="max-w-xs" />
          </div>
          {sources.map((s) => (
            <div key={s.source_id} className="flex items-center justify-between gap-3 text-sm border-t border-border pt-2">
              <div className="min-w-0 truncate">{s.name}</div>
              <div className="flex items-center gap-2 shrink-0">
                <Badge variant={s.reference_availability === "available" ? "secondary" : "outline"}>
                  {s.reference_availability ?? "restricted"}
                </Badge>
                <Button size="sm" variant="outline" disabled={referenceAvailabilityMutation.isPending}
                        onClick={() => referenceAvailabilityMutation.mutate({
                          sourceId: s.source_id,
                          reference_availability: s.reference_availability === "available" ? "restricted" : "available",
                          actor_id: stewardId,
                        })}>
                  {s.reference_availability === "available" ? "Restrictedへ変更" : "Availableへ変更"}
                </Button>
              </div>
            </div>
          ))}
        </div>

        <div className="space-y-2">
          {(documentsQuery.data?.documents ?? []).map((doc) => (
            <div key={doc.id} className="card-paper rounded-lg p-4 flex items-center justify-between gap-3">
              <div className="min-w-0">
                <div className="text-editorial text-base truncate">{doc.title || doc.id}</div>
                <div className="text-xs text-muted-foreground">{doc.url}</div>
                {doc.isMock && <Badge variant="outline" className="mt-1">mock</Badge>}
              </div>
              <div className="flex items-center gap-2 shrink-0">
                {chunkCounts[doc.id] !== undefined && (
                  <Badge variant="secondary">{t("geo.sourcesDocuments.chunkCount")}: {chunkCounts[doc.id]}</Badge>
                )}
                <Button size="sm" variant="outline" disabled={chunkMutation.isPending}
                        onClick={() => chunkMutation.mutate(doc.id, {
                          onSuccess: (result) => setChunkCounts((prev) => ({ ...prev, [doc.id]: result.chunks.length })),
                        })}>
                  {t("geo.sourcesDocuments.chunkButton")}
                </Button>
              </div>
            </div>
          ))}
        </div>
      </main>
    </>
  );
}
