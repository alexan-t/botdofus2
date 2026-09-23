"""Erreurs métier affichables sans traceback dans l'interface."""
class GameDataError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)
