import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useState } from "react";
import { Loader2, Plus } from "lucide-react";
import { TopBar } from "@/components/top-bar";
import { AiStatusBadge, ContactFormDialog, ErrorNote, LogFormDialog, UserNames } from "@/components/geo/external-network";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useDeactivateExtContact, useExtContact, useExtUsers } from "@/lib/geoExternalNetworkApi";
import { useLanguage } from "@/lib/i18n";

export const Route = createFileRoute("/geo/external-network/contacts/$contactId")({
  component: ExternalContactDetailPage,
});

function ExternalContactDetailPage() {
  const { contactId } = Route.useParams();
  const { t } = useLanguage();
  const navigate = useNavigate();
  const query = useExtContact(contactId);
  const me = useExtUsers().data?.me;
  const deactivate = useDeactivateExtContact();
  const [editOpen, setEditOpen] = useState(false);
  const [logOpen, setLogOpen] = useState(false);
  const c = query.data;

  return (
    <>
      <TopBar
        breadcrumb={[
          { label: t("nav.geoExternalNetwork") },
          { label: t("nav.geoExternalContacts"), href: "/geo/external-network/contacts" },
          { label: c?.name ?? "…" },
        ]}
      />
      <main className="flex-1 max-w-[1000px] w-full mx-auto px-8 py-8 space-y-6">
        {query.isLoading && <Loader2 className="h-5 w-5 animate-spin" />}
        {query.error && <ErrorNote error={query.error} />}
        {c && (
          <>
            <section className="border-b border-border pb-5 flex items-start justify-between gap-4">
              <div>
                <h1 className="text-editorial text-3xl">
                  {c.name}
                  {c.status === "inactive" && (
                    <Badge variant="secondary" className="ml-2 align-middle">
                      {t("extnet.status.inactive")}
                    </Badge>
                  )}
                </h1>
                <p className="text-sm text-muted-foreground mt-1">{c.affiliation_title ?? "—"}</p>
              </div>
              <div className="flex gap-2 shrink-0">
                {c.status === "active" && (
                  <Button onClick={() => setLogOpen(true)}>
                    <Plus className="h-4 w-4 mr-1" />
                    {t("extnet.action.addLog")}
                  </Button>
                )}
                {c.can_edit && (
                  <Button variant="outline" onClick={() => setEditOpen(true)}>
                    {t("extnet.action.edit")}
                  </Button>
                )}
                {c.can_edit && c.status === "active" && (
                  <Button
                    variant="outline"
                    disabled={deactivate.isPending}
                    onClick={() => window.confirm(t("extnet.deactivateConfirm")) && deactivate.mutate(c.contact_id)}
                  >
                    {t("extnet.action.deactivate")}
                  </Button>
                )}
              </div>
            </section>
            {deactivate.error && <ErrorNote error={deactivate.error} />}

            <section className="space-y-2">
              <h2 className="text-lg font-semibold">{t("extnet.detail.basic")}</h2>
              <dl className="grid grid-cols-[10rem_1fr] gap-y-2 text-sm">
                <dt className="text-muted-foreground">{t("extnet.field.expertise")}</dt>
                <dd className="whitespace-pre-wrap">
                  {c.expertise_note || <span className="text-muted-foreground">{t("extnet.detail.expertiseNone")}</span>}
                </dd>
                <dt className="text-muted-foreground">{t("extnet.field.owners")}</dt>
                <dd>
                  <UserNames ids={c.owner_ids} me={me} />
                </dd>
                <dt className="text-muted-foreground">{t("extnet.col.lastContact")}</dt>
                <dd>{c.last_contact_at ?? "—"}</dd>
                <dt className="text-muted-foreground">{t("extnet.col.count")}</dt>
                <dd>{c.log_count}</dd>
                <dt className="text-muted-foreground">{t("extnet.field.memo")}</dt>
                <dd className="whitespace-pre-wrap">
                  {c.memo_hidden ? <span className="text-muted-foreground">{t("extnet.detail.memoHidden")}</span> : (c.memo ?? "—")}
                </dd>
              </dl>
            </section>

            <section className="space-y-2">
              <h2 className="text-lg font-semibold">{t("extnet.detail.inferred")}</h2>
              {c.inferred_expertise && c.inferred_expertise.summarized_log_count > 0 ? (
                <div className="border border-border rounded-md px-4 py-3 space-y-2 bg-muted/30">
                  <p className="text-xs text-muted-foreground">{c.inferred_expertise.note}</p>
                  {(
                    [
                      ["topics", "extnet.detail.inferredTopics"],
                      ["regions", "extnet.detail.inferredRegions"],
                      ["actors", "extnet.detail.inferredActors"],
                    ] as const
                  ).map(([k, label]) =>
                    c.inferred_expertise![k].length > 0 ? (
                      <div key={k} className="text-sm flex flex-wrap items-center gap-1.5">
                        <span className="text-muted-foreground mr-1">{t(label)}</span>
                        {c.inferred_expertise![k].map((x) => (
                          <Badge key={x.name} variant="outline">
                            {x.name}
                            <span className="ml-1 text-muted-foreground">×{x.count}</span>
                          </Badge>
                        ))}
                      </div>
                    ) : null,
                  )}
                  <p className="text-xs text-muted-foreground">
                    {t("extnet.detail.inferredBasis", {
                      logs: c.inferred_expertise.log_count,
                      summarized: c.inferred_expertise.summarized_log_count,
                      views: c.inferred_expertise.speaker_view_count,
                    })}
                  </p>
                  {c.inferred_expertise.sample_views.length > 0 && (
                    <ul className="list-disc pl-5 text-sm space-y-0.5">
                      {c.inferred_expertise.sample_views.map((v, i) => (
                        <li key={i}>{v}</li>
                      ))}
                    </ul>
                  )}
                </div>
              ) : c.inferred_expertise ? (
                <p className="text-sm text-muted-foreground">
                  {t("extnet.detail.inferredNoSummary", { logs: c.inferred_expertise.log_count })}
                </p>
              ) : (
                <p className="text-sm text-muted-foreground">{t("extnet.detail.inferredNone")}</p>
              )}
            </section>

            <section className="space-y-2">
              <h2 className="text-lg font-semibold">{t("extnet.detail.history")}</h2>
              {c.logs.length === 0 && <p className="text-sm text-muted-foreground">{t("extnet.empty.history")}</p>}
              <ul className="space-y-2">
                {c.logs.map((l) => (
                  // カードのどこを押しても、面談ログの詳細が開く
                  <li
                    key={l.log_id}
                    className="border border-border rounded-md px-4 py-3 cursor-pointer hover:bg-muted/40"
                    onClick={() =>
                      navigate({
                        to: "/geo/external-network/logs/$logId",
                        params: { logId: l.log_id },
                      })
                    }
                  >
                    <div className="flex items-center gap-3 text-sm">
                      <Link to="/geo/external-network/logs/$logId" params={{ logId: l.log_id }} className="font-medium hover:underline">
                        {l.contact_date}
                      </Link>
                      <AiStatusBadge status={l.ai_status} />
                      <span className="text-muted-foreground truncate">
                        {l.contacts.map((x) => x.name).join("、")}
                      </span>
                    </div>
                    <p className="text-sm mt-1 text-muted-foreground line-clamp-2">
                      {l.summary_snippet ?? l.memo_head ?? "—"}
                    </p>
                  </li>
                ))}
              </ul>
            </section>

            <ContactFormDialog open={editOpen} onOpenChange={setEditOpen} contact={c} />
            <LogFormDialog open={logOpen} onOpenChange={setLogOpen} presetContactId={c.contact_id} />
          </>
        )}
      </main>
    </>
  );
}
