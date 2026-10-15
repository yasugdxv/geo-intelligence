import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { Loader2 } from "lucide-react";
import { TopBar } from "@/components/top-bar";
import { AiSummaryPanel, AttachmentList, ErrorNote, LogFormDialog, UserNames } from "@/components/geo/external-network";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useDeleteExtLog, useExtLog, useExtUsers } from "@/lib/geoExternalNetworkApi";
import { useLanguage } from "@/lib/i18n";

export const Route = createFileRoute("/geo/external-network/logs/$logId")({
  component: ExternalLogDetailPage,
});

// 表示順: 面談日 → 外部コンタクト → 社内参加者 → AI整理（要約・重要ポイント・見解・根拠/異論・注視点）→ 元メモ → 添付
function ExternalLogDetailPage() {
  const { logId } = Route.useParams();
  const { t } = useLanguage();
  const navigate = useNavigate();
  const query = useExtLog(logId);
  const me = useExtUsers().data?.me;
  const del = useDeleteExtLog();
  const [editOpen, setEditOpen] = useState(false);
  const log = query.data;

  return (
    <>
      <TopBar
        breadcrumb={[
          { label: t("nav.geoExternalNetwork") },
          { label: t("nav.geoExternalLogs"), href: "/geo/external-network/logs" },
          { label: log?.contact_date ?? "…" },
        ]}
      />
      <main className="flex-1 max-w-[1000px] w-full mx-auto px-8 py-8 space-y-6">
        {query.isLoading && <Loader2 className="h-5 w-5 animate-spin" />}
        {query.error && <ErrorNote error={query.error} />}
        {log && (
          <>
            <section className="border-b border-border pb-5 flex items-start justify-between gap-4">
              <div className="space-y-2">
                <h1 className="text-editorial text-3xl">{log.contact_date}</h1>
                <div className="flex flex-wrap gap-1.5">
                  {log.contacts.map((c) => (
                    <Link key={c.contact_id} to="/geo/external-network/contacts/$contactId" params={{ contactId: c.contact_id }}>
                      <Badge variant="secondary" className="hover:underline">
                        {c.name}
                        {c.affiliation_title ? `（${c.affiliation_title}）` : ""}
                      </Badge>
                    </Link>
                  ))}
                </div>
                <p className="text-sm text-muted-foreground">
                  {t("extnet.col.interviewers")}：<UserNames ids={log.participant_ids} me={me} />
                </p>
              </div>
              <div className="flex gap-2 shrink-0">
                {log.can_edit && (
                  <Button variant="outline" onClick={() => setEditOpen(true)}>
                    {t("extnet.action.edit")}
                  </Button>
                )}
                {log.can_delete && (
                  <Button
                    variant="outline"
                    disabled={del.isPending}
                    onClick={() =>
                      window.confirm(t("extnet.deleteLogConfirm")) &&
                      del.mutate(log.log_id, { onSuccess: () => navigate({ to: "/geo/external-network/logs" }) })
                    }
                  >
                    {t("extnet.action.delete")}
                  </Button>
                )}
              </div>
            </section>
            {del.error && <ErrorNote error={del.error} />}

            <section className="space-y-2">
              <h2 className="text-lg font-semibold">{t("extnet.ai.title")}</h2>
              <AiSummaryPanel log={log} />
            </section>

            <section className="space-y-2">
              <h2 className="text-lg font-semibold">{t("extnet.detail.originalMemo")}</h2>
              {log.memo ? (
                <p className="text-sm whitespace-pre-wrap border border-border rounded-md px-4 py-3">{log.memo}</p>
              ) : (
                <p className="text-sm text-muted-foreground">{t("extnet.detail.noMemo")}</p>
              )}
            </section>

            <section className="space-y-2">
              <h2 className="text-lg font-semibold">{t("extnet.detail.attachments")}</h2>
              <AttachmentList log={log} />
            </section>

            <LogFormDialog open={editOpen} onOpenChange={setEditOpen} log={log} />
          </>
        )}
      </main>
    </>
  );
}
