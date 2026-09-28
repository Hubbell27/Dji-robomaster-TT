/**
 * Downscale and re-encode a photo to JPEG in the browser before upload.
 * This keeps uploads small on cellular connections, converts iPhone HEIC to
 * JPEG, and drops EXIF metadata (e.g. GPS location). The server re-encodes
 * again, so this is a convenience, not the security boundary.
 */
export async function prepareCardPhoto(file: File, maxEdge = 2000): Promise<Blob> {
  const bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
  const scale = Math.min(1, maxEdge / Math.max(bitmap.width, bitmap.height));
  const w = Math.round(bitmap.width * scale);
  const h = Math.round(bitmap.height * scale);
  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  canvas.getContext("2d")!.drawImage(bitmap, 0, 0, w, h);
  bitmap.close();
  return new Promise((resolve, reject) =>
    canvas.toBlob((b) => (b ? resolve(b) : reject(new Error("encode failed"))), "image/jpeg", 0.85),
  );
}
