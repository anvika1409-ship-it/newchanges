/**
 * The multipart upload must carry credentials.
 *
 * `uploadImage` cannot go through `apiClient.request()` — a multipart body
 * needs the browser to set its own `Content-Type` boundary — so it builds its
 * headers by hand. It previously built them without the bearer token, and
 * every upload was refused with `missing_credentials` while the rest of the
 * application worked. Nothing caught it because no test covered this path.
 *
 * These tests pin the header, not the upload.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { setAuthToken } from './apiClient';
import { uploadImage } from './quality';

const TOKEN = 'test-token-not-a-credential';

function stubFetch() {
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({
      ref: 'data/uploads/abc.png',
      content_type: 'image/png',
      size_bytes: 70,
      classification: 'manufacturing_product_image',
    }),
  });
  vi.stubGlobal('fetch', fetchMock);
  vi.stubGlobal('crypto', { randomUUID: () => 'fixed-request-id' });
  return fetchMock;
}

function headersOf(fetchMock: ReturnType<typeof vi.fn>): Record<string, string> {
  return fetchMock.mock.calls[0][1].headers as Record<string, string>;
}

const file = new File([new Uint8Array([1, 2, 3])], 'part.png', { type: 'image/png' });

afterEach(() => {
  setAuthToken(null);
  vi.unstubAllGlobals();
});

describe('uploadImage', () => {
  it('sends the bearer token when one is set', async () => {
    const fetchMock = stubFetch();
    setAuthToken(TOKEN);

    await uploadImage(file);

    expect(headersOf(fetchMock).Authorization).toBe(`Bearer ${TOKEN}`);
  });

  it('sends no Authorization header when no token is set', async () => {
    const fetchMock = stubFetch();

    await uploadImage(file);

    // Not an empty or malformed header — absent. A `Bearer null` would be
    // refused the same way but would misreport the cause in the server log.
    expect(headersOf(fetchMock)).not.toHaveProperty('Authorization');
  });

  it('does not set Content-Type, so the browser can supply the boundary', async () => {
    const fetchMock = stubFetch();
    setAuthToken(TOKEN);

    await uploadImage(file);

    expect(headersOf(fetchMock)).not.toHaveProperty('Content-Type');
  });
});
