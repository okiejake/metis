"""The one error a household or the catalog raises, naming the item at fault."""


class HouseholdError(ValueError):
    def __init__(self, item: str, message: str):
        super().__init__(f"{item}: {message}")
        self.item = item
        self.message = message
