export function removeTrailingSlashToken(draft: string): string {
  return draft.replace(/(?:^|\s)\/\S*$/, "").trimEnd();
}
