# End-to-end smoke test against local backend + fixture book site (ASCII-safe).
$ErrorActionPreference = "Stop"
$base = "http://127.0.0.1:8000/api"

function Show($title, $obj) {
    Write-Host ""
    Write-Host "=== $title ==="
    $obj | ConvertTo-Json -Depth 6
}

# 1. health
$health = Invoke-RestMethod "$base/health"
Show "health" $health

# 2. login as seeded admin
$loginBody = @{ username = "admin"; password = "view123456" } | ConvertTo-Json
$login = Invoke-RestMethod -Method Post "$base/auth/login" -ContentType "application/json" -Body $loginBody
$tok = $login.token
Write-Host ""
Write-Host "login user: $($login.user.username) superuser=$($login.user.is_superuser)"
$H = @{ Authorization = "Bearer $tok" }

# 3. permission catalog
$cat = Invoke-RestMethod "$base/roles/permissions/catalog" -Headers $H
Write-Host "permissions declared: $($cat.items.Count)"

# 4. import sample source by URL
$impBody = @{ url = "http://127.0.0.1:8901/sample-source.json" } | ConvertTo-Json
$imp = Invoke-RestMethod -Method Post "$base/books/sources/import" -Headers $H -ContentType "application/json" -Body $impBody
Show "import" $imp

$sources = Invoke-RestMethod "$base/books/sources" -Headers $H
Show "sources" $sources

$engines = Invoke-RestMethod "$base/books/engines" -Headers $H
Show "engines" $engines

# 5. search (fixture site ignores query params)
$searchBody = @{ key = "demo"; page = 1 } | ConvertTo-Json
$search = Invoke-RestMethod -Method Post "$base/books/search" -Headers $H -ContentType "application/json" -Body $searchBody
Show "search items" $search.items
Show "search errors" $search.errors

# 6. info
$surl = [uri]::EscapeDataString("http://127.0.0.1:8901")
$burl = [uri]::EscapeDataString("http://127.0.0.1:8901/book/1.html")
$info = Invoke-RestMethod "$base/books/info?source_url=$surl&book_url=$burl" -Headers $H
Show "info" $info

# 7. toc
$turl = [uri]::EscapeDataString($info.tocUrl)
$toc = Invoke-RestMethod "$base/books/toc?source_url=$surl&toc_url=$turl" -Headers $H
Write-Host "toc total: $($toc.chapters.Count)"
Show "toc first+last" @($toc.chapters[0], $toc.chapters[$toc.chapters.Count - 1])

# 8. content of chapter 1 (has next page)
$curl = [uri]::EscapeDataString($toc.chapters[0].url)
$cbase = [uri]::EscapeDataString($toc.chapters[0].baseUrl)
$content = Invoke-RestMethod "$base/books/content?source_url=$surl&url=$curl&title=ch1&base=$cbase" -Headers $H
$len = [Math]::Min(200, $content.content.Length)
Show "content ch1 (head)" $content.content.Substring(0, $len)

# 9. shelf add + progress + me + dashboard
$shelfBody = @{
    bookUrl = "http://127.0.0.1:8901/book/1.html"
    tocUrl = $info.tocUrl
    name = "book-one"
    author = "author-one"
    sourceUrl = "http://127.0.0.1:8901"
} | ConvertTo-Json
$add = Invoke-RestMethod -Method Post "$base/books/shelf" -Headers $H -ContentType "application/json" -Body $shelfBody
Show "shelf add" $add

$progBody = @{
    bookUrl = "http://127.0.0.1:8901/book/1.html"
    chapterIndex = 0
    chapterTitle = "chapter-1"
} | ConvertTo-Json
$prog = Invoke-RestMethod -Method Post "$base/books/progress" -Headers $H -ContentType "application/json" -Body $progBody
Show "progress" $prog

$me = Invoke-RestMethod "$base/auth/me" -Headers $H
Show "me" $me

$dash = Invoke-RestMethod "$base/dashboard" -Headers $H
Show "dashboard" $dash

# 10. roles CRUD quick check
$roleBody = @{
    name = "editor"
    description = "test role"
    permissions = @("auth.basic", "books.search")
} | ConvertTo-Json
$newRole = Invoke-RestMethod -Method Post "$base/roles" -Headers $H -ContentType "application/json" -Body $roleBody
Show "new role" $newRole
$delRole = Invoke-RestMethod -Method Delete "$base/roles/$($newRole.id)" -Headers $H
Show "delete role" $delRole

# 11. users list & plugins list
$users = Invoke-RestMethod "$base/users" -Headers $H
Show "users" $users.items
$plugins = Invoke-RestMethod "$base/plugins" -Headers $H
Show "plugins" $plugins

Write-Host ""
Write-Host "E2E DONE"
