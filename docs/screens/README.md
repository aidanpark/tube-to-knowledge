# README screenshots (`docs/images/<lang>/screens.png`)

These screens are real t2k output: six public videos (`videos.txt`) were summarized by t2k and rendered by its site generator.
Summaries, categories and ratings are exactly what t2k produced. Thumbnails and videos belong to their creators.
The generated summaries are not stored in this repository.

## Reproduce

```sh
cp -r docs/screens/demo-en /tmp/t2k-demo        # one demo person: categories and interests (demo-ko for Korean)
export T2K_HOME=/tmp/t2k-demo
while read -r url; do t2k summarize "$url" --user demo; sleep 30; done < docs/screens/videos.txt
t2k site --user demo                             # /tmp/t2k-demo/users/demo/site
```

Then capture `index.html`, `v/Oaq4OPx4xlI.html` and `categories.html` at 390px wide, full page height, and place them side by side
(captured at 2x and scaled to 1800px wide). Claude writes the summaries, so the wording differs on every run.
