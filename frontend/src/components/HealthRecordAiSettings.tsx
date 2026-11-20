import { Alert, Checkbox, Stack, Text } from "@mantine/core";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { getAiVisibility, setAiVisibility, type AiVisibilityCategory } from "../api/client";

/** Owner opt-in, by category, for imported health records in chat and AI (ADR-0040). */
export function HealthRecordAiSettings(): React.JSX.Element {
  const queryClient = useQueryClient();
  const settings = useQuery({ queryKey: ["health-record-ai-visibility"], queryFn: getAiVisibility });
  const mutation = useMutation({
    mutationFn: ({ category, enabled }: { category: AiVisibilityCategory; enabled: boolean }) => setAiVisibility(category, enabled),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["health-record-ai-visibility"] }),
  });

  return (
    <section aria-labelledby="health-record-ai-heading">
      <h2 id="health-record-ai-heading">Imported health records in chat and AI</h2>
      <div className="settings-card">
        <Text>
          Records imported from your patient portals are hidden from chat and AI until you switch a category on. Switching one off hides it again for new questions.
        </Text>
        {settings.isPending ? <Text role="status">Loading choices…</Text> : null}
        {settings.isError ? <Alert color="red" role="alert">These choices could not be loaded.</Alert> : null}
        {settings.data === undefined ? null : (
          <Stack gap="sm" mt="md" component="fieldset" aria-describedby="health-record-medications-note">
            <legend>Categories chat and AI may read</legend>
            {settings.data.items.map((item) => (
              <Checkbox
                key={item.category}
                label={item.label}
                description={item.has_ai_path ? undefined : "No chat or AI feature reads this category yet."}
                checked={item.enabled}
                disabled={mutation.isPending}
                onChange={(event) => { mutation.mutate({ category: item.category, enabled: event.currentTarget.checked }); }}
              />
            ))}
            <Text id="health-record-medications-note" c="dimmed">{settings.data.medications_note}</Text>
          </Stack>
        )}
        {mutation.isError ? <Alert color="red" role="alert" mt="md">The choice was not saved. Nothing changed.</Alert> : null}
      </div>
    </section>
  );
}
