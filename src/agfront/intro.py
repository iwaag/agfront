"""`python -m agfront.intro`: post `params/intro.md` to `#agents` as this instance."""

from agag.agent import intro_main

from .instance import SPEC

if __name__ == "__main__":
    intro_main(SPEC)
