"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Link2, ShieldCheck, ShieldX } from "lucide-react";
import Link from "next/link";
import { Fragment, useState } from "react";
import { HashValue, JsonView } from "@/components/ui/evidence";
import { PageHeader } from "@/components/ui/layout";
import { Button, Card, CardHeader, Input, Select } from "@/components/ui/primitives";
import { Banner, EmptyState, ErrorState, LoadingRows } from "@/components/ui/states";
import { Tag } from "@/components/ui/status";
import { api, qs } from "@/lib/api";
import { fmtDateTime } from "@/lib/format";
import type { AuditEvent, Paged } from "@/lib/types";

export default function AuditPage() {
  const [action, setAction] = useState("");
  const [actorType, setActorType] = useState("");
  const [search, setSearch] = useState("");
  const [term, setTerm] = useState("");
  const [page, setPage] = useState(1);
  const [expanded, setExpanded] = useState<number | null>(null);
  const q = useQuery({
    queryKey: ["audit", action, actorType, term, page],
    queryFn: () => api.get<Paged<AuditEvent> & { actions: string[] }>(`/api/audit${qs({ action, actor_type: actorType, q: term, page, page_size: 50 })}`),
    placeholderData: keepPreviousData,
  });
  const chain = useQuery({ queryKey: ["audit-verify"], queryFn: () => api.get<{ valid: boolean; checked: number; head_hash?: string; broken_at_seq?: number }>("/api/audit/verify") });
  const pages = q.data ? Math.max(1, Math.ceil(q.data.total / q.data.page_size)) : 1;

  return (
    <>
      <PageHeader title="Audit Explorer" description="Append-only, hash-chained ledger of every consequential event. Application workflows cannot edit or delete entries; on PostgreSQL a trigger enforces this at the database level."
        actions={<Button onClick={() => chain.refetch()} loading={chain.isFetching} icon={<Link2 className="size-4" />}>Verify chain integrity</Button>} />
      {chain.data && (
        <div className="mb-4">
          {chain.data.valid
            ? <Banner tone="ok" title={`Hash chain intact — ${chain.data.checked} events verified`}>Head hash <span className="font-mono">{chain.data.head_hash?.slice(0, 20)}…</span></Banner>
            : <Banner tone="bad" title={`Hash chain broken at sequence ${chain.data.broken_at_seq}`}>An entry was altered outside the application. Investigate immediately.</Banner>}
        </div>
      )}
      <Card>
        <CardHeader title="Events" actions={<>
          <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); setTerm(search); setPage(1); }}>
            <Input aria-label="Search actor, operation or action" placeholder="Actor / operation / action" value={search} onChange={(e) => setSearch(e.target.value)} className="w-56" />
          </form>
          <Select aria-label="Action" value={action} onChange={(e) => { setAction(e.target.value); setPage(1); }}>
            <option value="">All actions</option>{q.data?.actions.map((a) => <option key={a} value={a}>{a}</option>)}
          </Select>
          <Select aria-label="Actor type" value={actorType} onChange={(e) => { setActorType(e.target.value); setPage(1); }}>
            <option value="">All actors</option><option value="USER">Users</option><option value="AGENT">Agent</option><option value="SYSTEM">System</option>
          </Select>
        </>} />
        {q.isError ? <div className="p-4"><ErrorState error={q.error} /></div> : !q.data ? <LoadingRows /> : q.data.items.length === 0 ? <EmptyState title="No events match" /> : (
          <>
            <div className="overflow-x-auto">
              <table className="data-table w-full">
                <thead><tr><th>Seq</th><th>Time</th><th>Actor</th><th>Action</th><th>Entity</th><th>Context</th><th>Hash</th></tr></thead>
                <tbody>{q.data.items.map((e) => (
                  <Fragment key={e.id}>
                    <tr className="cursor-pointer" onClick={() => setExpanded(expanded === e.id ? null : e.id)}>
                      <td className="font-mono text-[12px] tabular">{e.seq}</td>
                      <td className="whitespace-nowrap text-[12px]">{fmtDateTime(e.occurred_at, undefined, { second: "2-digit" })}</td>
                      <td><Tag tone={e.actor_type === "AGENT" ? "accent" : e.actor_type === "USER" ? "info" : "neutral"}>{e.actor_type}</Tag> <span className="font-mono text-[11.5px] text-muted">{e.actor_id}</span></td>
                      <td className="font-mono text-[12px] font-medium">{e.action}</td>
                      <td className="text-[12px] text-muted">{e.entity_type ? `${e.entity_type} #${e.entity_id}` : "—"}</td>
                      <td className="text-[12px]">
                        {e.drive_id && <Link onClick={(ev) => ev.stopPropagation()} className="mr-2 text-accent hover:underline" href={`/drives/${e.drive_id}`}>drive #{e.drive_id}</Link>}
                        {e.run_id && <Link onClick={(ev) => ev.stopPropagation()} className="mr-2 text-accent hover:underline" href={`/agent/${e.run_id}`}>run #{e.run_id}</Link>}
                        {e.operation_id && <span className="font-mono text-muted">op {e.operation_id.slice(0, 8)}</span>}
                      </td>
                      <td><HashValue value={e.hash} n={8} /></td>
                    </tr>
                    {expanded === e.id && (
                      <tr><td colSpan={7} className="bg-subtle">
                        <div className="grid gap-3 md:grid-cols-2">
                          <JsonView value={e.details} label="Details (redacted)" defaultOpen />
                          <div className="space-y-1 text-[12px]"><p>Previous hash: <HashValue value={e.prev_hash} n={24} /></p><p>This hash: <HashValue value={e.hash} n={24} /></p>
                            <p className="text-muted">hash = SHA-256(prev_hash ‖ SHA-256(canonical event body))</p></div>
                        </div>
                      </td></tr>
                    )}
                  </Fragment>
                ))}</tbody>
              </table>
            </div>
            <div className="flex items-center justify-between border-t border-line px-3 py-2 text-xs text-muted">
              <span className="flex items-center gap-1.5">{chain.data?.valid ? <ShieldCheck className="size-3.5 text-ok" /> : <ShieldX className="size-3.5 text-faint" />}{q.data.total} events · page {page} of {pages}</span>
              <div className="flex gap-1"><Button size="sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</Button><Button size="sm" disabled={page >= pages} onClick={() => setPage(page + 1)}>Next</Button></div>
            </div>
          </>
        )}
      </Card>
    </>
  );
}
