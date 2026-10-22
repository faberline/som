// Shows a failed request. This file is the same in every generated product.
import type { ApiError } from "../api.ts";

export function ErrorView({ error }: { error: ApiError | null }) {
  if (error === null) {
    return null;
  }
  return (
    <div role="alert" className="error">
      <p>
        {error.status === 0 ? "No response" : `HTTP ${error.status}`}:{" "}
        {error.detail}
      </p>
      {error.otherErrors.length > 0 && (
        <ul>
          {error.otherErrors.map((message, index) => (
            <li key={index}>{message}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
