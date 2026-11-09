import type { ModelOption } from "../../lib/schema";
import { Input, Select } from "../../ui/field";

/** The provider's models; a plain field when there is no list. */
export function ModelPicker({
  value,
  options,
  disabled,
  label,
  onChange,
}: {
  value: string;
  options: ModelOption[] | null;
  disabled: boolean;
  label: string;
  onChange: (value: string) => void;
}) {
  if (!options?.length) {
    return (
      <Input
        value={value}
        onChange={(event) => onChange(event.target.value)}
        disabled={disabled}
        required
        aria-label={label}
        className="font-mono text-[12.5px]"
      />
    );
  }
  const listed = options.some((option) => option.id === value);
  return (
    <Select
      value={value}
      onChange={(event) => onChange(event.target.value)}
      disabled={disabled}
      required
      aria-label={label}
    >
      {value && !listed ? <option value={value}>{value}</option> : null}
      {value ? null : <option value="">Choose a model</option>}
      {options.map((option) => (
        <option key={option.id} value={option.id}>
          {option.name}
        </option>
      ))}
    </Select>
  );
}
