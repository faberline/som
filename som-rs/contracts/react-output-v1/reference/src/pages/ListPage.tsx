// Lists rows with the prompt's filters and pages. This file is the same in every generated product.
import { useEffect, useState } from "react";
import type { SubmitEvent } from "react";
import { Link, useSearchParams } from "react-router";
import type { ApiError } from "../api.ts";
import { listRows } from "../api.ts";
import { display, own } from "../form.ts";
import type { ListQuery, Row } from "../schema.ts";
import { FIELDS, QUERY, RESOURCE } from "../schema.ts";
import type { Value } from "../spec.ts";
import { ErrorView } from "./ErrorView.tsx";

// The list query for the page's search parameters, which use the API parameter names.
export function queryOf(search: URLSearchParams): ListQuery {
  const query: Record<string, string | number | boolean> = {};
  if (QUERY === null) {
    return query as ListQuery;
  }
  const filter = QUERY.filter;
  const text = filter === null ? null : search.get(filter.param);
  if (filter !== null && text !== null && text !== "") {
    if (filter.type !== "bool") {
      query[filter.param] = text;
    } else if (text === "true" || text === "false") {
      query[filter.param] = text === "true";
    }
  }
  const before = search.get("before");
  if (QUERY.before !== null && before !== null && before !== "") {
    query.before = before;
  }
  query.limit = QUERY.pageSize;
  const offset = Number(search.get("offset") ?? "0");
  query.offset = Number.isInteger(offset) && offset > 0 ? offset : 0;
  return query as ListQuery;
}

function Filters() {
  const [search, setSearch] = useSearchParams();
  if (QUERY === null || (QUERY.filter === null && QUERY.before === null)) {
    return null;
  }
  const filter = QUERY.filter;
  const submit = (event: SubmitEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const next = new URLSearchParams();
    for (const [key, value] of form) {
      if (typeof value === "string" && value !== "") {
        next.set(key, value);
      }
    }
    setSearch(next);
  };
  return (
    <form className="filters" onSubmit={submit} key={search.toString()}>
      {filter !== null && (
        <label>
          {filter.field}
          {filter.type === "bool" ? (
            <select
              name={filter.param}
              defaultValue={search.get(filter.param) ?? ""}
            >
              <option value="">any</option>
              <option value="true">true</option>
              <option value="false">false</option>
            </select>
          ) : (
            <input
              name={filter.param}
              defaultValue={search.get(filter.param) ?? ""}
            />
          )}
        </label>
      )}
      {QUERY.before !== null && (
        <label>
          {QUERY.before} before
          <input
            type="date"
            name="before"
            defaultValue={search.get("before") ?? ""}
          />
        </label>
      )}
      <button type="submit">Apply</button>
    </form>
  );
}

export function ListPage() {
  const [search, setSearch] = useSearchParams();
  const [rows, setRows] = useState<Row[] | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const query = queryOf(search);
  const key = JSON.stringify(query);

  useEffect(() => {
    let live = true;
    setRows(null);
    setError(null);
    void listRows(JSON.parse(key) as ListQuery).then((result) => {
      if (!live) {
        return;
      }
      if (result.ok) {
        setRows(result.value);
      } else {
        setError(result.error);
      }
    });
    return () => {
      live = false;
    };
  }, [key]);

  const page = (offset: number) => {
    const next = new URLSearchParams(search);
    if (offset > 0) {
      next.set("offset", String(offset));
    } else {
      next.delete("offset");
    }
    setSearch(next);
  };

  const offset = query.offset ?? 0;
  const pageSize = QUERY === null ? null : QUERY.pageSize;
  return (
    <section>
      <div className="toolbar">
        <h1>{RESOURCE.name} list</h1>
        <Link to="/new">New {RESOURCE.name}</Link>
      </div>
      <Filters />
      <ErrorView error={error} />
      {rows === null && error === null && <p>Loading…</p>}
      {rows !== null && rows.length === 0 && <p>No rows.</p>}
      {rows !== null && rows.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>id</th>
              {FIELDS.map((field) => (
                <th key={field.name}>{field.name}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id}>
                <td>
                  <Link to={`/${row.id}`}>{row.id}</Link>
                </td>
                {FIELDS.map((field) => (
                  <td key={field.name}>
                    {display(own(row as Record<string, Value>, field.name))}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {pageSize !== null && (
        <nav className="pager">
          <button
            type="button"
            disabled={offset === 0}
            onClick={() => page(Math.max(0, offset - pageSize))}
          >
            Previous
          </button>
          <button
            type="button"
            disabled={rows === null || rows.length < pageSize}
            onClick={() => page(offset + pageSize)}
          >
            Next
          </button>
        </nav>
      )}
    </section>
  );
}
