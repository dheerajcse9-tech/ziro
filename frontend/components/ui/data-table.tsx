"use client";

import {
  type ColumnDef,
  flexRender,
  getCoreRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  type SortingState,
  useReactTable,
} from "@tanstack/react-table";
import { ArrowDown, ArrowUp, ChevronsUpDown } from "lucide-react";
import { useRouter } from "next/navigation";
import * as React from "react";
import { cn } from "@/lib/format";
import { Button } from "./primitives";
import { EmptyState } from "./states";

export function DataTable<T>({ data, columns, rowHref, pageSize, empty, initialSort, dense, rowClassName }: {
  data: T[];
  columns: ColumnDef<T, any>[];
  rowHref?: (row: T) => string | undefined;
  pageSize?: number;
  empty?: React.ReactNode;
  initialSort?: SortingState;
  dense?: boolean;
  rowClassName?: (row: T) => string | undefined;
}) {
  const [sorting, setSorting] = React.useState<SortingState>(initialSort ?? []);
  const router = useRouter();
  const table = useReactTable({
    data, columns, state: { sorting }, onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(), getSortedRowModel: getSortedRowModel(),
    ...(pageSize ? { getPaginationRowModel: getPaginationRowModel(), initialState: { pagination: { pageSize } } } : {}),
  });
  if (data.length === 0) return <>{empty ?? <EmptyState title="Nothing to show" />}</>;
  return (
    <div>
      <div className="overflow-x-auto">
        <table className={cn("data-table w-full border-collapse", dense && "[&_td]:py-1.5")}>
          <thead>
            {table.getHeaderGroups().map((hg) => (
              <tr key={hg.id}>
                {hg.headers.map((h) => {
                  const sortable = h.column.getCanSort();
                  const dir = h.column.getIsSorted();
                  return (
                    <th key={h.id} style={{ width: h.getSize() !== 150 ? h.getSize() : undefined }}
                      aria-sort={dir === "asc" ? "ascending" : dir === "desc" ? "descending" : undefined}>
                      {h.isPlaceholder ? null : sortable ? (
                        <button type="button" onClick={h.column.getToggleSortingHandler()} className="inline-flex items-center gap-1 uppercase hover:text-ink">
                          {flexRender(h.column.columnDef.header, h.getContext())}
                          {dir === "asc" ? <ArrowUp className="size-3" /> : dir === "desc" ? <ArrowDown className="size-3" /> : <ChevronsUpDown className="size-3 opacity-40" />}
                        </button>
                      ) : flexRender(h.column.columnDef.header, h.getContext())}
                    </th>
                  );
                })}
              </tr>
            ))}
          </thead>
          <tbody>
            {table.getRowModel().rows.map((row) => {
              const href = rowHref?.(row.original);
              return (
                <tr key={row.id}
                  className={cn(href && "cursor-pointer", rowClassName?.(row.original))}
                  onClick={href ? (e) => { if (!(e.target as HTMLElement).closest("a,button,input,select")) router.push(href); } : undefined}
                  onKeyDown={href ? (e) => { if (e.key === "Enter") router.push(href); } : undefined}
                  tabIndex={href ? 0 : undefined}>
                  {row.getVisibleCells().map((cell) => <td key={cell.id}>{flexRender(cell.column.columnDef.cell, cell.getContext())}</td>)}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {pageSize && table.getPageCount() > 1 && (
        <div className="flex items-center justify-between border-t border-line px-3 py-2 text-xs text-muted">
          <span className="tabular">Page {table.getState().pagination.pageIndex + 1} of {table.getPageCount()} · {data.length} rows</span>
          <div className="flex gap-1">
            <Button size="sm" onClick={() => table.previousPage()} disabled={!table.getCanPreviousPage()}>Previous</Button>
            <Button size="sm" onClick={() => table.nextPage()} disabled={!table.getCanNextPage()}>Next</Button>
          </div>
        </div>
      )}
    </div>
  );
}
