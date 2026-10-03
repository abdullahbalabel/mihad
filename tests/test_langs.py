import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mihad_memory import langs  # noqa: E402

SAMPLES = {
    "typescript": ('''import { x } from "./x";

export function clamp(v: number, lo: number, hi: number): number {
  if (v < lo) { return lo; }
  return v > hi ? hi : v;  // "}" in a comment
}

export const firstWord = (text: string): string => {
  const parts = text.split(" ");
  doSomething(parts, {
    keep: true,
  });
  return parts[0];
};

export class Cart {
  private items: number[] = [];

  add(price: number): void {
    this.items.push(price);
  }

  async total(discount = 0): Promise<number> {
    const s = "{ not a brace }";
    return this.items.reduce((a, b) => a + b, 0) * (1 - discount);
  }
}
''', {"clamp": 4, "firstWord": 11, "add": 20, "total": 25, "Cart": 18}),
    "java": ('''package shop;

public class Cart {
    private final List<Integer> items = new ArrayList<>();

    public void add(int price) {
        items.add(price);
    }

    public int total(int discount) throws IllegalArgumentException {
        if (discount < 0) {
            throw new IllegalArgumentException("negative");
        }
        return items.stream().mapToInt(i -> i).sum();
    }
}
''', {"add": 7, "total": 14, "Cart": 4}),
    "csharp": ('''namespace Shop
{
    public class Cart
    {
        public void Add(int price)
        {
            items.Add(price);
        }

        public static int Total(List<int> items, int discount = 0)
        {
            return items.Sum() * (100 - discount) / 100;
        }
    }
}
''', {"Add": 7, "Total": 12, "Cart": 4}),
    "go": ('''package shop

type Cart struct {
	items []int
}

func (c *Cart) Add(price int) {
	c.items = append(c.items, price)
}

func FirstWord(text string) string {
	parts := strings.Fields(text)
	if len(parts) == 0 {
		return ""
	}
	return parts[0]
}
''', {"Add": 8, "FirstWord": 15, "Cart": 4}),
    "rust": ('''pub struct Cart {
    items: Vec<i64>,
}

impl Cart {
    pub fn add(&mut self, price: i64) {
        self.items.push(price);
    }

    pub fn total(&self) -> i64 {
        self.items.iter().sum()
    }
}

pub fn first_word(text: &str) -> &str {
    text.split_whitespace().next().unwrap_or("")
}
''', {"add": 7, "total": 11, "first_word": 16, "Cart": 2}),
    "php": ('''<?php
class Cart {
    private $items = [];

    public function add($price) {
        $this->items[] = $price;
    }
}

function first_word($text) {
    $parts = explode(" ", $text);
    return $parts[0] ?? "";
}
''', {"add": 6, "first_word": 12, "Cart": 3}),
    "ruby": ('''class Cart
  def initialize
    @items = []
  end

  def add(price)
    @items << price
  end
end

def first_word(text)
  text.split.first || ""
end
''', {"add": 7, "first_word": 12, "initialize": 3}),
    "kotlin": ('''class Cart {
    private val items = mutableListOf<Int>()

    fun add(price: Int) {
        items.add(price)
    }
}

fun firstWord(text: String): String {
    return text.split(" ").firstOrNull() ?: ""
}
''', {"add": 5, "firstWord": 10, "Cart": 2}),
    "cpp": ('''#include <string>

class Cart {
public:
    void add(int price) {
        items.push_back(price);
    }
};

std::string first_word(const std::string& text) {
    auto pos = text.find(' ');
    return text.substr(0, pos);
}
''', {"add": 6, "first_word": 12, "Cart": 4}),
}


class LangTests(unittest.TestCase):
    def test_innermost_symbol_for_changed_lines(self):
        for lang, (src, expect) in SAMPLES.items():
            for name, line in expect.items():
                with self.subTest(lang=lang, name=name):
                    self.assertEqual(langs.symbols_at(src, lang, [line]), {name})

    def test_calls_and_callbacks_are_not_declarations(self):
        src = SAMPLES["typescript"][0]
        self.assertNotIn("doSomething", langs.defs(src, "typescript"))
        self.assertNotIn("reduce", langs.defs(src, "typescript"))
        self.assertNotIn("add", langs.defs(SAMPLES["java"][0].replace("items.add(price);", "x();"), "java")
                         .keys() - {"add"})

    def test_language_of_paths_and_tests(self):
        self.assertEqual(langs.language_of("src/a.ts"), "typescript")
        self.assertEqual(langs.language_of("src/a.mjs"), "javascript")
        self.assertTrue(langs.is_test_path("src/cart.test.ts", "typescript"))
        self.assertTrue(langs.is_test_path("pkg/cart_test.go", "go"))
        self.assertTrue(langs.is_test_path("src/test/java/shop/CartTest.java", "java"))
        self.assertFalse(langs.is_test_path("src/main/java/shop/Cart.java", "java"))

    def test_focused_commands(self):
        self.assertEqual(langs.focused_command("node", ["test/a.test.mjs"]), ["node", "--test", "test/a.test.mjs"])
        self.assertEqual(langs.focused_command("go", ["./pkg::TestA|TestB"]),
                         ["go", "test", "./pkg", "-run", "^(TestA|TestB)$"])
        self.assertIn("-Dtest=CartTest", langs.focused_command("maven", ["CartTest"]))

    def test_python_unchanged(self):
        src = "def a():\n    return 1\n\n\nclass B:\n    def m(self):\n        pass\n"
        self.assertEqual(langs.defs(src, "python"), {"a": (1, 2), "B": (5, 7)})
        self.assertEqual(langs.symbols_at(src, "python", [7]), {"B"})


if __name__ == "__main__":
    unittest.main()
