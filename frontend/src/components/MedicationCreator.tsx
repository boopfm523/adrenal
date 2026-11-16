import { useMutation, useQueryClient } from "@tanstack/react-query";

import { createMedication, type Medication } from "../api/client";

function formString(data: FormData, name: string): string {
  const value = data.get(name);
  return typeof value === "string" ? value : "";
}

export function MedicationCreator({ onCreated }: { onCreated: (medication: Medication) => void }): React.JSX.Element {
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: createMedication,
    onSuccess: async (medication) => {
      await queryClient.invalidateQueries({ queryKey: ["medications"] });
      onCreated(medication);
    },
  });
  return <details className="nested-form">
    <summary>Add a medication to the list</summary>
    <form className="aligned-form-grid plan-form-grid" onSubmit={(event) => {
      event.preventDefault();
      const form = event.currentTarget;
      const data = new FormData(form);
      const strength = formString(data, "strength").trim();
      mutation.mutate({
        name: formString(data, "name"),
        formulation: formString(data, "formulation") || null,
        strength: strength === "" ? null : strength,
        strength_unit: formString(data, "strength_unit") || null,
        default_unit: data.get("default_unit") as "mg" | "mcg" | "ml" | "tablet",
        default_route: data.get("default_route") as "oral" | "intramuscular" | "subcutaneous" | "intravenous",
        active_from: null,
        active_to: null,
        notes: null,
      });
    }}>
      <label>Name<input name="name" required maxLength={200} /></label>
      <label>Formulation<input name="formulation" placeholder="tablet" maxLength={120} /></label>
      <label>Strength<input name="strength" type="number" min="0.0001" step="any" inputMode="decimal" /></label>
      <label>Strength unit<input name="strength_unit" maxLength={16} placeholder="mg" /></label>
      <label>Default dose unit<select name="default_unit" defaultValue="mg"><option value="mg">mg</option><option value="mcg">mcg</option><option value="ml">mL</option><option value="tablet">tablet</option></select></label>
      <label>Default route<select name="default_route" defaultValue="oral"><option value="oral">Oral</option><option value="intramuscular">Intramuscular</option><option value="subcutaneous">Subcutaneous</option><option value="intravenous">Intravenous</option></select></label>
      <button type="submit" disabled={mutation.isPending}>Save medication</button>
      {mutation.isError ? <p className="error-summary" role="alert">Medication was not saved. Check for a duplicate name and strength.</p> : null}
    </form>
  </details>;
}
