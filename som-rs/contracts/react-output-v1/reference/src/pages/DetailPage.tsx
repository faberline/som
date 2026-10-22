// Shows one row and deletes it. This file is the same in every generated product.
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import type { ApiError } from "../api.ts";
import { deleteRow, getRow } from "../api.ts";
import { display, own } from "../form.ts";
import type { Row } from "../schema.ts";
import { FIELDS, RESOURCE } from "../schema.ts";
import type { Value } from "../spec.ts";
import { ErrorView } from "./ErrorView.tsx";

// The row id of a client route, or null when the segment is not a positive integer.
export function idOf(segment: string | undefined): number | null {
  if (segment === undefined || !/^[1-9][0-9]{0,9}$/.test(segment)) {
    return null;
  }
  const id = Number(segment);
  return id <= 2147483647 ? id : null;
}

export function DetailPage() {
  const params = useParams();
  const navigate = useNavigate();
  const id = idOf(params.id);
  const [row, setRow] = useState<Row | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let live = true;
    setRow(null);
    setError(null);
    if (id === null) {
      return;
    }
    void getRow(id).then((result) => {
      if (!live) {
        return;
      }
      if (result.ok) {
        setRow(result.value);
      } else {
        setError(result.error);
      }
    });
    return () => {
      live = false;
    };
  }, [id]);

  if (id === null) {
    return <p role="alert">Page not found.</p>;
  }

  const remove = async () => {
    if (!window.confirm(`Delete ${RESOURCE.name} ${id}?`)) {
      return;
    }
    setBusy(true);
    const result = await deleteRow(id);
    setBusy(false);
    if (result.ok) {
      void navigate("/");
    } else {
      setError(result.error);
    }
  };

  const values = row as Record<string, Value> | null;
  return (
    <section>
      <div className="toolbar">
        <h1>
          {RESOURCE.name} {id}
        </h1>
        <Link to="/">Back to list</Link>
      </div>
      <ErrorView error={error} />
      {row === null && error === null && <p>Loading…</p>}
      {values !== null && (
        <>
          <dl>
            <div className="pair">
              <dt>id</dt>
              <dd>{display(own(values, "id"))}</dd>
            </div>
            {FIELDS.map((field) => (
              <div key={field.name} className="pair">
                <dt>{field.name}</dt>
                <dd>{display(own(values, field.name))}</dd>
              </div>
            ))}
            {RESOURCE.timestamps && (
              <>
                <div className="pair">
                  <dt>created_at</dt>
                  <dd>{display(own(values, "created_at"))}</dd>
                </div>
                <div className="pair">
                  <dt>updated_at</dt>
                  <dd>{display(own(values, "updated_at"))}</dd>
                </div>
              </>
            )}
          </dl>
          <div className="actions">
            <Link to={`/${id}/edit`}>Edit</Link>
            <button type="button" disabled={busy} onClick={() => void remove()}>
              Delete
            </button>
          </div>
        </>
      )}
    </section>
  );
}
