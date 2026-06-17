export class StatsValidationError extends Error {
  category: string;

  constructor(category: string, message: string) {
    super(message);
    this.name = "StatsValidationError";
    this.category = category;
  }
}
