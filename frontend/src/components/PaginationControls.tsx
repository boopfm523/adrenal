import { Button, Group, Text } from "@mantine/core";

export interface PageMetadata {
  page: number;
  page_size: number;
  total_items: number;
  total_pages: number;
}

interface PaginationControlsProps {
  label: string;
  metadata: PageMetadata;
  onPageChange: (page: number) => void;
}

export function PaginationControls({ label, metadata, onPageChange }: PaginationControlsProps): React.JSX.Element | null {
  if (metadata.total_items === 0) return null;
  const first = (metadata.page - 1) * metadata.page_size + 1;
  const last = Math.min(metadata.page * metadata.page_size, metadata.total_items);

  return (
    <nav className="pagination" aria-label={`${label} pagination`}>
      <Text className="pagination__status" role="status" aria-live="polite">
        Showing {first}–{last} of {metadata.total_items}. Page {metadata.page} of {metadata.total_pages}.
      </Text>
      <Group className="pagination__actions" gap="sm">
        <Button type="button" variant="outline" disabled={metadata.page === 1} onClick={() => { onPageChange(metadata.page - 1); }}>
          Previous
        </Button>
        <Button type="button" variant="outline" disabled={metadata.page === metadata.total_pages} onClick={() => { onPageChange(metadata.page + 1); }}>
          Next
        </Button>
      </Group>
    </nav>
  );
}
